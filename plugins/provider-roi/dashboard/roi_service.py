"""Read-only data and private-state service for the Provider ROI dashboard."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from hermes_constants import get_hermes_home
from hermes_cli import kanban_db
from hermes_cli import kanban_diagnostics as diagnostics

TORONTO = ZoneInfo("America/Toronto")
TRANSITION_MONTH = "2026-07"
MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_SOURCES = 64

# Classification is deliberately plugin-local. billing_mode records accounting
# provenance, not a flat-rate/PAYG business contract.
DEFAULT_PROVIDER_RULES: dict[str, dict[str, Any]] = {
    "openrouter": {"classification": "payg", "included": False, "cancellable": False},
    "nous": {"classification": "critical", "included": True, "cancellable": False, "unique_role": True, "label": "Nous Portal"},
    "openai-codex": {"classification": "flat_rate", "included": True, "cancellable": True},
    "anthropic": {"classification": "flat_rate", "included": True, "cancellable": True},
    "kimi-coding": {"classification": "flat_rate", "included": True, "cancellable": True},
    "opencode-zen": {"classification": "flat_rate", "included": True, "cancellable": True},
}


def store_path(home: Path | None = None) -> Path:
    return (home or get_hermes_home()) / "plugins" / "provider-roi" / "state.json"


def default_state() -> dict[str, Any]:
    return {"schema_version": 1, "providers": {}, "exceptions": {}, "manual_quotas": {}, "snapshots": {}}


def load_state(home: Path | None = None) -> dict[str, Any]:
    path = store_path(home)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state()
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return default_state()
    state = default_state()
    for key in state:
        if isinstance(value.get(key), type(state[key])):
            state[key] = value[key]
    return state


def save_state(state: dict[str, Any], home: Path | None = None) -> None:
    path = store_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".provider-roi-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass


def validate_provider_id(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if not PROVIDER_RE.fullmatch(normalized):
        raise ValueError("provider id must match [a-z0-9][a-z0-9._-]{0,63}")
    return normalized


def validate_month(month: str | None, now: datetime | None = None) -> str:
    if month is None or month == "":
        return (now or datetime.now(TORONTO)).astimezone(TORONTO).strftime("%Y-%m")
    if not MONTH_RE.fullmatch(month):
        raise ValueError("month must use YYYY-MM")
    return month


def month_bounds(month: str) -> tuple[float, float]:
    year, mon = (int(part) for part in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=TORONTO)
    end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=TORONTO)
    return start.timestamp(), end.timestamp()


def previous_month(month: str) -> str:
    year, mon = (int(part) for part in month.split("-"))
    return f"{year - (mon == 1):04d}-{12 if mon == 1 else mon - 1:02d}"


def profile_homes(home: Path | None = None) -> list[tuple[str, Path]]:
    current = home or get_hermes_home()
    found: dict[Path, str] = {current.resolve(): "active"}
    try:
        from hermes_cli.profiles import list_profiles
        for profile in list_profiles():
            found[Path(profile.path).resolve()] = profile.name
    except Exception:
        pass
    return sorted(((name, path) for path, name in found.items()), key=lambda item: item[0])


def _readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def read_usage(month: str, homes: Iterable[tuple[str, Path]] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    start, end = month_bounds(month)
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    sources = list(homes or profile_homes())
    if len(sources) > MAX_SOURCES:
        warnings.append(f"profile scan capped at {MAX_SOURCES} sources")
    for profile, home in sources[:MAX_SOURCES]:
        db_path = home / "state.db"
        if not db_path.exists():
            continue
        try:
            conn = _readonly(db_path)
            try:
                query = """
                    SELECT billing_provider, model, task, SUM(api_call_count) AS api_calls,
                           SUM(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens + reasoning_tokens) AS tokens,
                           SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)) AS cost_usd
                    FROM session_model_usage
                    WHERE last_seen >= ? AND last_seen < ?
                    GROUP BY billing_provider, model, task
                """
                for row in conn.execute(query, (start, end)):
                    rows.append({"profile": profile, **dict(row)})
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            warnings.append(f"profile {profile}: usage unavailable ({exc})")
    return rows, warnings


def _board_db_path(board: str) -> Path:
    if board == kanban_db.DEFAULT_BOARD:
        return kanban_db.kanban_home() / "kanban.db"
    return kanban_db.board_dir(board) / "kanban.db"


def read_kanban() -> tuple[dict[str, int], list[str]]:
    totals = {"delivered": 0, "approved": 0, "friction": 0}
    warnings: list[str] = []
    try:
        boards = kanban_db.list_boards(include_archived=False)
    except Exception as exc:
        return totals, [f"boards unavailable ({exc})"]
    if len(boards) > MAX_SOURCES:
        warnings.append(f"board scan capped at {MAX_SOURCES} sources")
    for board_meta in boards[:MAX_SOURCES]:
        board = str(board_meta.get("slug") or kanban_db.DEFAULT_BOARD)
        path = _board_db_path(board)
        if not path.exists():
            continue
        try:
            conn = _readonly(path)
            try:
                tasks = conn.execute("SELECT * FROM tasks WHERE status != 'archived'").fetchall()
                totals["delivered"] += sum(task["status"] in {"review", "done"} for task in tasks)
                totals["approved"] += sum(task["status"] == "done" for task in tasks)
                events = defaultdict(list)
                runs = defaultdict(list)
                for row in conn.execute("SELECT * FROM task_events ORDER BY id"):
                    events[row["task_id"]].append(row)
                for row in conn.execute("SELECT * FROM task_runs ORDER BY id"):
                    runs[row["task_id"]].append(row)
                for task in tasks:
                    totals["friction"] += sum(
                        item.count for item in diagnostics.compute_task_diagnostics(
                            task, events[task["id"]], runs[task["id"]]
                        )
                    )
            finally:
                conn.close()
        except (sqlite3.Error, OSError) as exc:
            warnings.append(f"board {board}: diagnostics unavailable ({exc})")
    return totals, warnings


def provider_rule(provider: str, state: dict[str, Any]) -> dict[str, Any]:
    rule = dict(DEFAULT_PROVIDER_RULES.get(provider, {"classification": "unclassified", "included": False, "cancellable": False}))
    custom = state.get("providers", {}).get(provider, {})
    if isinstance(custom, dict):
        rule.update({key: custom[key] for key in ("classification", "included", "cancellable", "monthly_cost_usd", "unique_role", "label") if key in custom})
    return rule


def is_low_value(item: dict[str, Any]) -> bool:
    return item["activity"] < 5 and item["delivered"] == 0 and item["approved"] == 0


def verdict(item: dict[str, Any], state: dict[str, Any], month: str) -> dict[str, str]:
    provider = item["provider"]
    rule = item["rule"]
    exception = state.get("exceptions", {}).get(provider)
    today = datetime.now(TORONTO).date().isoformat()
    if isinstance(exception, dict) and exception.get("reason") and exception.get("expires_on", "") >= today:
        return {"action": "keep", "reason": "active exception"}
    if rule["classification"] == "payg" or not rule.get("included"):
        return {"action": "exclude", "reason": "PAYG or unclassified provider is outside ROI"}
    if rule["classification"] == "critical":
        return {"action": "keep", "reason": "critical infrastructure; never auto-cancel"}
    if item["friction"] > max(2, item["activity"] // 10):
        return {"action": "reassign", "reason": "derived Kanban friction exceeds activity"}
    if is_low_value(item):
        previous = state.get("snapshots", {}).get(previous_month(month), {})
        prior = next(
            (candidate for candidate in previous.get("providers", []) if candidate.get("provider") == provider),
            {},
        ) if isinstance(previous, dict) else {}
        if prior.get("low_value") and not rule.get("unique_role"):
            return {"action": "cancel", "reason": "two low months and no unique role"}
        return {"action": "keep", "reason": "first low month: observe"}
    if float(rule.get("monthly_cost_usd") or 0) > 0 and item["activity"] < 20:
        return {"action": "downgrade", "reason": "low activity versus configured fixed cost"}
    return {"action": "keep", "reason": "activity and delivery justify retention"}


def build_live_overview(month: str, state: dict[str, Any]) -> dict[str, Any]:
    usage_rows, warnings = read_usage(month)
    kanban, kanban_warnings = read_kanban()
    warnings.extend(kanban_warnings)
    grouped: dict[str, dict[str, Any]] = {}
    for row in usage_rows:
        provider = str(row.get("billing_provider") or "unknown").strip().lower() or "unknown"
        item = grouped.setdefault(provider, {"provider": provider, "models": [], "profiles": set(), "activity": 0, "tokens": 0, "usage_cost_usd": 0.0})
        item["activity"] += int(row.get("api_calls") or 0)
        item["tokens"] += int(row.get("tokens") or 0)
        item["usage_cost_usd"] += float(row.get("cost_usd") or 0)
        item["profiles"].add(row["profile"])
        item["models"].append({"model": row.get("model") or "unknown", "task": row.get("task") or "main", "api_calls": int(row.get("api_calls") or 0)})
    providers = []
    for provider, item in sorted(grouped.items()):
        item["profiles"] = sorted(item["profiles"])
        item["rule"] = provider_rule(provider, state)
        item["manual_quota"] = state.get("manual_quotas", {}).get(provider)
        item["exception"] = state.get("exceptions", {}).get(provider)
        item["delivered"] = kanban["delivered"]
        item["approved"] = kanban["approved"]
        item["friction"] = kanban["friction"]
        item["low_value"] = is_low_value(item)
        item["verdict"] = verdict(item, state, month)
        providers.append(item)
    return {
        "month": month,
        "generated_at": int(time.time()),
        "providers": providers,
        "summary": {"activity": sum(item["activity"] for item in providers), "delivered": kanban["delivered"], "approved": kanban["approved"], "friction": kanban["friction"]},
        "warnings": warnings,
        "provenance": {"activity": "session_model_usage.api_call_count", "delivered": "Kanban tasks in review or done", "approved": "Kanban tasks in done", "friction": "count of compute_task_diagnostics findings"},
    }


def overview(month: str | None = None, *, now: datetime | None = None, home: Path | None = None) -> dict[str, Any]:
    current = validate_month(None, now)
    requested = validate_month(month, now)
    state = load_state(home)
    closed = requested < current and requested >= TRANSITION_MONTH
    snapshot = state["snapshots"].get(requested)
    if closed and isinstance(snapshot, dict):
        return {**snapshot, "snapshot": True, "store_path": str(store_path(home))}
    live = build_live_overview(requested, state)
    if closed:
        # Store the rendered report intact. Verdict history reads low_value from
        # this immutable report; freezing must not turn the UI's provider list
        # into an internal index.
        state["snapshots"][requested] = live
        save_state(state, home)
    return {**live, "snapshot": False, "store_path": str(store_path(home))}


def update_state(payload: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    state = load_state(home)
    provider = validate_provider_id(payload.get("provider", ""))
    kind = payload.get("kind")
    if kind == "provider":
        allowed = {"classification", "included", "cancellable", "monthly_cost_usd", "unique_role", "label"}
        values = {key: payload[key] for key in allowed if key in payload}
        if values.get("classification") not in {None, "flat_rate", "payg", "critical", "unclassified"}:
            raise ValueError("invalid classification")
        if "monthly_cost_usd" in values and (not isinstance(values["monthly_cost_usd"], (int, float)) or values["monthly_cost_usd"] < 0):
            raise ValueError("monthly_cost_usd must be a positive number")
        state["providers"][provider] = {**state["providers"].get(provider, {}), **values}
    elif kind == "exception":
        reason = str(payload.get("reason") or "").strip()
        expires_on = str(payload.get("expires_on") or "")
        if not reason or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", expires_on):
            raise ValueError("exception requires reason and expires_on YYYY-MM-DD")
        state["exceptions"][provider] = {"reason": reason[:500], "expires_on": expires_on}
    elif kind == "manual_quota":
        used = payload.get("used_percent")
        if not isinstance(used, (int, float)) or not 0 <= used <= 100:
            raise ValueError("used_percent must be between 0 and 100")
        state["manual_quotas"][provider] = {"used_percent": used, "recorded_at": int(time.time())}
    else:
        raise ValueError("kind must be provider, exception, or manual_quota")
    save_state(state, home)
    return state
