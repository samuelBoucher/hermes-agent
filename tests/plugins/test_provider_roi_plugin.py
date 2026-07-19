"""Behavior and safety tests for the Provider ROI dashboard plugin."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
import sqlite3
import stat
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "provider-roi" / "dashboard" / "plugin_api.py"


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    spec = importlib.util.spec_from_file_location(f"provider_roi_{id(tmp_path)}", PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, home


def _usage_db(home: Path, *, provider: str = "anthropic", calls: int = 0, estimated: float = 0, actual: float = 0, status: str = "estimated") -> None:
    conn = sqlite3.connect(home / "state.db")
    conn.execute("""CREATE TABLE session_model_usage (
        session_id TEXT, billing_provider TEXT, model TEXT, task TEXT, api_call_count INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
        cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL,
        actual_cost_usd REAL, cost_status TEXT)""")
    conn.execute("INSERT INTO session_model_usage VALUES ('session-1', ?, 'model', '', ?, 1, 2, 0, 0, 0, ?, ?, ?)", (provider, calls, estimated, actual, status))
    conn.commit()
    conn.close()


def _replace_usage(home: Path, *, calls: int, estimated: float, actual: float, status: str) -> None:
    conn = sqlite3.connect(home / "state.db")
    conn.execute("UPDATE session_model_usage SET api_call_count=?, estimated_cost_usd=?, actual_cost_usd=?, cost_status=?", (calls, estimated, actual, status))
    conn.commit()
    conn.close()


def _row(calls: int, estimated: float = 0, actual: float = 0, status: str = "estimated", provider: str = "anthropic") -> dict[str, object]:
    return {"profile": "active", "session_id": "s", "billing_provider": provider, "model": "m", "task": "", "api_call_count": calls, "input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "estimated_cost_usd": estimated, "actual_cost_usd": actual, "cost_status": status}


def test_rollup_excludes_cross_month_delta_and_marks_transition_partial(plugin):
    api, _ = plugin
    state = api.service.default_state()
    june = datetime(2026, 6, 30, 23, tzinfo=api.service.TORONTO).timestamp()
    july = datetime(2026, 7, 2, tzinfo=api.service.TORONTO).timestamp()
    api.service.observe_usage([_row(10)], state, june)
    api.service.observe_usage([_row(15)], state, july)

    assert state["monthly_usage"].get("2026-07", {}) == {}
    assert state["unattributed"]["2026-07"][0]["reason"] == "coverage_gap"
    state["providers"]["anthropic"] = {}
    report = api.service.build_live_overview("2026-07", state, observed_at=july)
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert report["coverage"]["status"] == "partial"
    assert provider["verdict"]["action"] != "cancel"


def test_rollup_uses_estimated_until_actual_cost_is_confirmed(plugin):
    api, _ = plugin
    state = api.service.default_state()
    observed = datetime(2026, 8, 2, tzinfo=api.service.TORONTO).timestamp()
    api.service.observe_usage([_row(10)], state, observed)
    api.service.observe_usage([_row(15, estimated=2, actual=0, status="estimated")], state, observed + 60)
    api.service.observe_usage([_row(20, estimated=3, actual=1, status="confirmed")], state, observed + 120)
    record = next(iter(state["monthly_usage"]["2026-08"].values()))

    assert record["usage_cost_usd"] == 3
    assert record["cost_status"] == "actual"


def test_global_kanban_is_not_duplicated_across_providers(plugin, monkeypatch):
    api, _ = plugin
    state = api.service.default_state()
    observed = datetime(2026, 8, 2, tzinfo=api.service.TORONTO).timestamp()
    api.service.observe_usage([_row(1, provider="anthropic"), _row(1, provider="openai-codex")], state, observed)
    monkeypatch.setattr(api.service, "read_usage", lambda: ([], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 4, "approved": 3, "friction": 2}, []))

    report = api.service.build_live_overview("2026-08", state, observed_at=observed)

    assert report["summary"]["delivered"] == 4
    assert all(item["delivered"] == item["approved"] == item["friction"] == 0 for item in report["providers"])
    assert report["provenance"]["kanban"] == "global only; task/provider attribution unavailable"


def test_openrouter_and_nous_rules_cannot_be_overridden_or_cancelled(plugin):
    api, home = plugin
    for provider in ("openrouter", "nous"):
        with pytest.raises(ValueError, match="immutable"):
            api.service.update_state({"kind": "provider", "provider": provider, "classification": "flat_rate", "cancellable": True}, home)
    state = api.service.default_state()
    state["providers"]["nous"] = {"classification": "flat_rate", "cancellable": True}
    state["snapshots"]["2026-08"] = {"providers": [{"provider": "nous", "low_value": True}]}
    item = {"provider": "nous", "rule": api.service.provider_rule("nous", state), "activity": 0, "delivered": 0, "approved": 0, "friction": 0, "coverage": "complete"}

    assert item["rule"]["classification"] == "critical"
    assert item["rule"]["cancellable"] is False
    assert api.service.verdict(item, state, "2026-09")["action"] == "keep"


def test_private_store_rejects_symlinks_and_sets_private_permissions(plugin, tmp_path):
    api, home = plugin
    api.service.update_state({"kind": "manual_quota", "provider": "anthropic", "used_percent": 20}, home)
    state_path = api.service.store_path(home)
    lock_path = state_path.with_name(".state.lock")
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700

    state_path.unlink()
    state_path.symlink_to(tmp_path / "outside.json")
    with pytest.raises(ValueError, match="symlink"):
        api.service.load_state(home)
    state_path.unlink()
    lock_path.unlink()
    lock_path.symlink_to(tmp_path / "outside.lock")
    with pytest.raises(ValueError, match="symlink"):
        api.service.update_state({"kind": "manual_quota", "provider": "anthropic", "used_percent": 30}, home)

    other_home = tmp_path / "other-home"
    other_home.mkdir()
    (other_home / "plugins").symlink_to(tmp_path / "outside-directory", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        api.service.load_state(other_home)


def test_store_transaction_prevents_lost_updates_and_preserves_snapshot(plugin, monkeypatch):
    api, home = plugin
    def write(provider: str) -> None:
        api.service.update_state({"kind": "manual_quota", "provider": provider, "used_percent": 20}, home)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(write, ("anthropic", "openai-codex")))
    state = api.service.load_state(home)
    assert set(state["manual_quotas"]) == {"anthropic", "openai-codex"}

    monkeypatch.setattr(api.service, "build_live_overview", lambda month, state, **kwargs: {"month": month, "providers": [], "summary": {}, "warnings": [], "provenance": {}})
    august = datetime(2026, 8, 1, tzinfo=api.service.TORONTO)
    first = api.service.overview("2026-07", now=august, home=home)
    api.service.update_state({"kind": "manual_quota", "provider": "anthropic", "used_percent": 40}, home)
    second = api.service.overview("2026-07", now=august, home=home)
    assert first["snapshot"] is False
    assert second["snapshot"] is True


def test_configured_external_only_provider_appears_with_zero_usage_and_plan(plugin, monkeypatch):
    api, home = plugin
    plan = {"amount": 20, "currency": "USD", "monthly_cost_cad": 28, "cost_status": "estimated", "renewal_on": None, "status": "active", "note": "external"}
    api.service.update_state({"kind": "provider", "provider": "anthropic", "plan": plan}, home)
    monkeypatch.setattr(api.service, "read_usage", lambda: ([], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))

    report = api.service.build_live_overview("2026-08", api.service.load_state(home))
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert provider["activity"] == 0
    assert provider["rule"]["plan"] == plan


@pytest.mark.parametrize("payload", [
    {"kind": "exception", "provider": "anthropic", "reason": "x" * 501, "expires_on": "2026-08-01"},
    {"kind": "exception", "provider": "anthropic", "reason": "x", "expires_on": "2026-02-30"},
    {"kind": "manual_quota", "provider": "anthropic", "used_percent": 10, "extra": "forbidden"},
])
def test_settings_validation_rejects_overlong_invalid_date_and_extra_fields(plugin, payload):
    api, _ = plugin
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/provider-roi")
    response = TestClient(app).post("/api/plugins/provider-roi/settings", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_settings_model_rejects_nonfinite_costs(plugin, value):
    api, _ = plugin
    with pytest.raises(ValueError):
        api.StoreUpdate.model_validate({"kind": "manual_quota", "provider": "anthropic", "used_percent": value})


def test_sqlite_readonly_uri_encodes_reserved_path_characters(plugin, tmp_path):
    api, _ = plugin
    path = tmp_path / "state ?# database.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE proof (value TEXT)")
    conn.execute("INSERT INTO proof VALUES ('ok')")
    conn.commit()
    conn.close()
    readonly = api.service._readonly(path)
    try:
        assert readonly.execute("SELECT value FROM proof").fetchone()["value"] == "ok"
        with pytest.raises(sqlite3.OperationalError):
            readonly.execute("INSERT INTO proof VALUES ('blocked')")
    finally:
        readonly.close()
