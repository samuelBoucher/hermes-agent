"""Behavior tests for the Provider ROI dashboard plugin."""
from __future__ import annotations

import importlib.util
import sqlite3
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


def _usage_db(home: Path, rows: list[tuple[str, str, int, float, float]]):
    conn = sqlite3.connect(home / "state.db")
    conn.execute("""CREATE TABLE session_model_usage (
        billing_provider TEXT, model TEXT, task TEXT, api_call_count INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
        cache_write_tokens INTEGER, reasoning_tokens INTEGER, actual_cost_usd REAL,
        estimated_cost_usd REAL, last_seen REAL)""")
    for provider, model, calls, cost, seen in rows:
        conn.execute("INSERT INTO session_model_usage VALUES (?, ?, '', ?, 1, 2, 0, 0, 0, ?, ?, ?)", (provider, model, calls, cost, cost, seen))
    conn.commit()
    conn.close()


def test_usage_uses_explicit_provider_classification_not_billing_mode(plugin, monkeypatch):
    api, home = plugin
    start = datetime(2026, 7, 1, tzinfo=api.service.TORONTO).timestamp()
    _usage_db(home, [("openrouter", "x/model", 25, 3.5, start + 60), ("openai-codex", "gpt", 12, 0, start + 60)])
    monkeypatch.setattr(api.service, "profile_homes", lambda: [("active", home)])
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 1, "approved": 1, "friction": 0}, []))

    report = api.service.build_live_overview("2026-07", api.service.default_state())
    providers = {item["provider"]: item for item in report["providers"]}

    assert providers["openrouter"]["rule"]["classification"] == "payg"
    assert providers["openrouter"]["verdict"]["action"] == "exclude"
    assert providers["openai-codex"]["rule"]["classification"] == "flat_rate"
    assert providers["openai-codex"]["activity"] == 12


def test_cross_profile_reader_caps_sources_and_fails_open(plugin):
    api, home = plugin
    start = datetime(2026, 7, 1, tzinfo=api.service.TORONTO).timestamp()
    other = home.parent / "other"
    other.mkdir()
    _usage_db(home, [("anthropic", "claude", 4, 0, start + 60)])
    (other / "state.db").write_text("not sqlite", encoding="utf-8")

    rows, warnings = api.service.read_usage("2026-07", [("active", home), ("other", other)])
    _, capped_warnings = api.service.read_usage("2026-07", [(str(index), other) for index in range(api.service.MAX_SOURCES + 1)])

    assert rows[0]["profile"] == "active"
    assert rows[0]["api_calls"] == 4
    assert warnings and "profile other" in warnings[0]
    assert capped_warnings[0] == "profile scan capped at 64 sources"


def test_read_kanban_aggregates_multiple_boards_read_only(plugin):
    api, _ = plugin
    from hermes_cli import kanban_db as kb

    first = kb.connect()
    first_id = kb.create_task(first, title="accepted", assignee="marek")
    first.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (first_id,))
    first.commit()
    first.close()
    kb.create_board("second")
    second = kb.connect(board="second")
    second_id = kb.create_task(second, title="awaiting review", assignee="marek")
    second.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (second_id,))
    second.commit()
    second.close()

    totals, warnings = api.service.read_kanban()

    assert totals["delivered"] == 2
    assert totals["approved"] == 1
    assert warnings == []


def test_closed_month_snapshot_is_immutable_after_first_following_month_load(plugin, monkeypatch):
    api, home = plugin
    calls = []
    monkeypatch.setattr(api.service, "build_live_overview", lambda month, state: calls.append(month) or {
        "month": month, "generated_at": len(calls), "providers": [], "summary": {}, "warnings": [], "provenance": {}
    })
    august = datetime(2026, 8, 1, tzinfo=api.service.TORONTO)

    first = api.service.overview("2026-07", now=august, home=home)
    second = api.service.overview("2026-07", now=august, home=home)

    assert first["snapshot"] is False
    assert second["snapshot"] is True
    assert second["generated_at"] == 1
    assert calls == ["2026-07"]


def test_verdicts_cover_two_low_months_exception_payg_and_critical(plugin):
    api, _ = plugin
    state = api.service.default_state()
    state["snapshots"]["2026-07"] = {"providers": [{"provider": "anthropic", "low_value": True}]}
    low = {"provider": "anthropic", "rule": api.service.provider_rule("anthropic", state), "activity": 0, "delivered": 0, "approved": 0, "friction": 0}
    assert api.service.verdict(low, state, "2026-08")["action"] == "cancel"
    payg = {**low, "provider": "openrouter", "rule": api.service.provider_rule("openrouter", state)}
    assert api.service.verdict(payg, state, "2026-08")["action"] == "exclude"
    critical = {**low, "provider": "nous", "rule": api.service.provider_rule("nous", state)}
    assert api.service.verdict(critical, state, "2026-08")["action"] == "keep"
    state["exceptions"]["anthropic"] = {"reason": "client migration", "expires_on": "2099-01-01"}
    assert api.service.verdict(low, state, "2026-08")["reason"] == "active exception"


def test_private_store_api_rejects_path_traversal_and_records_manual_quota(plugin):
    api, home = plugin
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/provider-roi")
    client = TestClient(app)

    bad = client.post("/api/plugins/provider-roi/settings", json={"kind": "manual_quota", "provider": "../../state", "used_percent": 20})
    good = client.post("/api/plugins/provider-roi/settings", json={"kind": "manual_quota", "provider": "kimi-coding", "used_percent": 20})

    assert bad.status_code == 400
    assert good.status_code == 200
    state = api.service.load_state(home)
    assert state["manual_quotas"]["kimi-coding"]["used_percent"] == 20
    assert api.service.store_path(home) == home / "plugins" / "provider-roi" / "state.json"
