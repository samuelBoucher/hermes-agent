"""Private, process-safe state storage for Provider ROI."""
from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from hermes_constants import get_hermes_home

_STATE_FILE = "state.json"
_LOCK_FILE = ".state.lock"


def store_path(home: Path | None = None) -> Path:
    return (home or get_hermes_home()) / "plugins" / "provider-roi" / _STATE_FILE


def default_state() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "providers": {},
        "exceptions": {},
        "manual_quotas": {},
        "snapshots": {},
        "observations": {},
        "monthly_usage": {},
        "unattributed": {},
    }


def _reject_symlink(path: Path, *, directory: bool = False) -> None:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise ValueError(f"Provider ROI store rejects symlink: {path}")
    if directory and not stat.S_ISDIR(mode):
        raise ValueError(f"Provider ROI store parent is not a directory: {path}")
    if not directory and not stat.S_ISREG(mode):
        raise ValueError(f"Provider ROI store file is not regular: {path}")


def _private_directory(path: Path) -> None:
    _reject_symlink(path, directory=True)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _reject_symlink(path, directory=True)
    os.chmod(path, 0o700)


def _paths(home: Path | None = None) -> tuple[Path, Path, Path]:
    root = home or get_hermes_home()
    _reject_symlink(root, directory=True)
    plugins = root / "plugins"
    _private_directory(plugins)
    directory = plugins / "provider-roi"
    _private_directory(directory)
    resolved_root = root.resolve(strict=True)
    resolved_directory = directory.resolve(strict=True)
    if resolved_directory.parent.parent != resolved_root:
        raise ValueError("Provider ROI store escapes Hermes home")
    state = directory / _STATE_FILE
    lock = directory / _LOCK_FILE
    _reject_symlink(state)
    _reject_symlink(lock)
    return directory, state, lock


def _read_state(state_path: Path, directory_fd: int) -> dict[str, Any]:
    try:
        fd = os.open(_STATE_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except FileNotFoundError:
        return default_state()
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default_state()
    if not isinstance(value, dict):
        return default_state()
    state = default_state()
    # Version one deliberately has no observation history: retaining its old
    # monthly numbers would fabricate attribution, so only safe local config is
    # migrated. The next observation starts new, explicitly partial coverage.
    if value.get("schema_version") not in {1, 2}:
        return state
    for key, default in state.items():
        if isinstance(value.get(key), type(default)):
            state[key] = value[key]
    return state


def _save_state(state: dict[str, Any], directory: Path, directory_fd: int) -> None:
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".provider-roi-", suffix=".json", dir=directory)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _reject_symlink(temporary_path)
        os.replace(temporary_path.name, _STATE_FILE, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def state_transaction(home: Path | None = None, *, save: bool = True) -> Iterator[dict[str, Any]]:
    """Hold an interprocess lock across a load/mutate/snapshot/save cycle."""
    directory, state_path, lock_path = _paths(home)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock_fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _read_state(state_path, directory_fd)
        yield state
        if save:
            _save_state(state, directory, directory_fd)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            os.close(directory_fd)


def load_state(home: Path | None = None) -> dict[str, Any]:
    with state_transaction(home, save=False) as state:
        return state


def save_state(state: dict[str, Any], home: Path | None = None) -> None:
    with state_transaction(home) as current:
        current.clear()
        current.update(state)
