"""Behavior and safety tests for the Provider ROI dashboard plugin."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
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


def _usage_db(
    home: Path,
    *,
    provider: str = "anthropic",
    calls: int = 0,
    estimated: float = 0,
    actual: float = 0,
    status: str = "estimated",
    with_task: bool = True,
) -> None:
    conn = sqlite3.connect(home / "state.db")
    task_column = ", task TEXT" if with_task else ""
    task_value = ", ''" if with_task else ""
    conn.execute(f"""CREATE TABLE session_model_usage (
        session_id TEXT, billing_provider TEXT, model TEXT{task_column}, api_call_count INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
        cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL,
        actual_cost_usd REAL, cost_status TEXT)""")
    conn.execute(
        f"INSERT INTO session_model_usage VALUES ('session-1', ?, 'model'{task_value}, ?, 1, 2, 0, 0, 0, ?, ?, ?)",
        (provider, calls, estimated, actual, status),
    )
    conn.commit()
    conn.close()


def _replace_usage(home: Path, *, calls: int, estimated: float, actual: float, status: str) -> None:
    conn = sqlite3.connect(home / "state.db")
    conn.execute("UPDATE session_model_usage SET api_call_count=?, estimated_cost_usd=?, actual_cost_usd=?, cost_status=?", (calls, estimated, actual, status))
    conn.commit()
    conn.close()


def _row(calls: int, estimated: float = 0, actual: float = 0, status: str = "estimated", provider: str = "anthropic") -> dict[str, object]:
    return {"profile": "active", "session_id": "s", "billing_provider": provider, "model": "m", "task": "", "api_call_count": calls, "input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "estimated_cost_usd": estimated, "actual_cost_usd": actual, "cost_status": status}


def test_fresh_store_carry_in_is_derived_from_persisted_baselines(plugin, monkeypatch):
    api, _ = plugin
    july = datetime(2026, 7, 2, tzinfo=api.service.TORONTO).timestamp()
    july_start, _ = api.service.month_bounds("2026-07")
    rows = [
        {**_row(4, provider="anthropic"), "session_id": "pre-a", "first_seen": july_start - 3600, "last_seen": july_start + 60, "input_tokens": 100_000, "output_tokens": 0},
        {**_row(2, provider="anthropic"), "session_id": "pre-b", "first_seen": july_start - 7200, "last_seen": july_start + 120, "input_tokens": 56_982, "output_tokens": 0},
    ]
    monkeypatch.setattr(api.service, "read_usage", lambda: (rows, []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))

    state = api.service.default_state()
    first = api.service.build_live_overview("2026-07", state, observed_at=july)
    second = api.service.build_live_overview("2026-07", state, observed_at=july + 60)
    carry = [{"provider": "anthropic", "api_calls": 6, "tokens": 156_982, "rows": 2, "semantics": "cumulative_at_baseline", "attributable_to_month": False}]

    assert first["summary"]["activity"] == second["summary"]["activity"] == 0
    assert first["summary"]["carry_in_activity"] == second["summary"]["carry_in_activity"] == 6
    assert first["summary"]["carry_in_semantics"] == "cumulative_at_baseline; not monthly activity"
    assert first["coverage"]["carry_ins"] == second["coverage"]["carry_ins"] == carry
    assert state["monthly_usage"].get("2026-07", {}) == {}
    assert first["coverage"]["status"] == "partial"
    assert "carry" in first["provenance"]["carry_ins"]


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
    assert report["coverage"]["carry_ins"] == []
    assert report["summary"]["carry_in_activity"] == 0
    assert provider["activity"] == 0
    assert provider["verdict"]["action"] != "cancel"


def test_historical_inactive_baseline_is_excluded(plugin, monkeypatch):
    api, _ = plugin
    july = datetime(2026, 7, 2, tzinfo=api.service.TORONTO).timestamp()
    july_start, _ = api.service.month_bounds("2026-07")
    row = {**_row(6), "first_seen": july_start - 7200, "last_seen": july_start - 1}
    monkeypatch.setattr(api.service, "read_usage", lambda: ([row], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))

    state = api.service.default_state()
    report = api.service.build_live_overview("2026-07", state, observed_at=july)

    assert report["summary"]["activity"] == report["summary"]["carry_in_activity"] == 0
    assert report["coverage"]["carry_ins"] == []
    assert state["unattributed"]["2026-07"][0]["reason"] == "historical_inactive"


def test_identical_refresh_keeps_breakdown_cardinality_stable(plugin, monkeypatch):
    """R2 regression: re-observing unchanged rows must not fabricate zero-activity
    monthly records for identities that are not month-scoped."""
    api, _ = plugin
    june = datetime(2026, 6, 15, tzinfo=api.service.TORONTO).timestamp()
    july = datetime(2026, 7, 2, tzinfo=api.service.TORONTO).timestamp()
    # 11 out-of-month identities, unscoped (no session/first_seen timestamps),
    # on a distinct model so a leaked zero record would change the breakdown.
    out_of_month = [{**_row(1), "session_id": f"old-{index}", "model": "old"} for index in range(11)]
    # 31 in-month identities establish a stable, non-zero breakdown.
    july_start, _ = api.service.month_bounds("2026-07")
    in_month = [
        {**_row(1), "session_id": f"july-{index}", "session_started_at": july_start + 60}
        for index in range(31)
    ]
    state = api.service.default_state()
    api.service.observe_usage(out_of_month, state, june)
    # First July observation: unchanged out-of-month counters must be skipped,
    # while July-scoped sessions record their baseline.
    api.service.observe_usage(out_of_month + in_month, state, july)
    monkeypatch.setattr(api.service, "read_usage", lambda: (out_of_month + in_month, []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))

    first = api.service.build_live_overview("2026-07", state, observed_at=july + 60)
    second = api.service.build_live_overview("2026-07", state, observed_at=july + 120)

    def cardinality(report):
        provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
        return len(provider["models"]), provider["activity"]

    assert cardinality(first) == cardinality(second) == (1, 31)
    assert len(state["monthly_usage"]["2026-07"]) == 31
    assert second["summary"]["activity"] == 31
    # Sum invariants: provider totals equal the summary; no zero-call pair leaks in.
    provider = next(item for item in second["providers"] if item["provider"] == "anthropic")
    assert sum(pair["api_calls"] for pair in provider["models"]) == provider["activity"]
    assert all(pair["api_calls"] > 0 for pair in provider["models"])


def test_read_usage_accepts_legacy_schema_without_task(plugin):
    api, home = plugin
    _usage_db(home, provider="custom", calls=7, with_task=False)

    rows, warnings = api.service.read_usage([("legacy", home)])

    assert warnings == []
    assert rows == [
        {
            "profile": "legacy",
            "session_id": "session-1",
            "billing_provider": "custom",
            "model": "model",
            "task": "",
            "api_call_count": 7,
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "estimated_cost_usd": 0,
            "actual_cost_usd": 0,
            "cost_status": "estimated",
            "cost_source": "",
            "first_seen": None,
            "last_seen": None,
            "session_started_at": None,
        }
    ]


def test_initial_month_scoped_usage_is_visible_but_partial(plugin, monkeypatch):
    api, _ = plugin
    observed = datetime(2026, 7, 18, tzinfo=api.service.TORONTO).timestamp()
    july_start, _ = api.service.month_bounds("2026-07")
    rows = [
        {**_row(12, provider="opencode-go"), "session_id": "july", "session_started_at": july_start},
        {**_row(99, provider="opencode-go"), "session_id": "june", "session_started_at": july_start - 1},
    ]
    monkeypatch.setattr(api.service, "read_usage", lambda: (rows, []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))

    state = api.service.default_state()
    report = api.service.build_live_overview("2026-07", state, observed_at=observed)
    provider = next(item for item in report["providers"] if item["provider"] == "opencode-go")

    assert provider["activity"] == report["summary"]["activity"] == 12
    assert state["observations"][next(key for key in state["observations"] if "|july|" in key)]["months"]["2026-07"]["state"] == "month_scoped"
    assert report["coverage"]["status"] == "partial"
    assert provider["rule"]["classification"] == "flat_rate"
    assert provider["verdict"]["action"] != "cancel"


def test_baseline_month_boundaries_are_half_open(plugin):
    api, _ = plugin
    observed = datetime(2026, 8, 2, tzinfo=api.service.TORONTO).timestamp()
    start, end = api.service.month_bounds("2026-08")

    assert api.service.classify_initial_baseline({**_row(1), "session_started_at": start}, "2026-08", observed)["state"] == "month_scoped"
    assert api.service.classify_initial_baseline({**_row(1), "session_started_at": end}, "2026-08", observed)["state"] == "unknown_timestamp"


@pytest.mark.parametrize(
    ("row_update", "expected"),
    [
        ({"first_seen": None, "session_started_at": None}, "unknown_timestamp"),
        ({"first_seen": "not-a-date", "session_started_at": None}, "unknown_timestamp"),
        ({"first_seen": float("nan"), "session_started_at": None}, "unknown_timestamp"),
        ({"first_seen": 9_999_999_999, "session_started_at": None}, "unknown_timestamp"),
        ({"first_seen": 10, "last_seen": 9}, "unknown_timestamp"),
        ({"first_seen": 10, "last_seen": 20, "api_call_count": -1}, "invalid_counter"),
    ],
)
def test_invalid_baselines_are_bounded_and_unattributed(plugin, row_update, expected):
    api, _ = plugin
    observed = datetime(2026, 8, 2, tzinfo=api.service.TORONTO).timestamp()
    row = {**_row(1), "first_seen": observed - 20, "last_seen": observed - 10, **row_update}
    state = api.service.default_state()

    api.service.observe_usage([row], state, observed)

    assert state["unattributed"]["2026-08"][0]["reason"] == expected
    assert state["monthly_usage"]["2026-08"] == {}


def test_counter_reset_rebaselines_without_subtraction(plugin):
    api, _ = plugin
    observed = datetime(2026, 8, 2, tzinfo=api.service.TORONTO).timestamp()
    start, _ = api.service.month_bounds("2026-08")
    state = api.service.default_state()
    baseline = {**_row(10), "session_started_at": start + 1}
    api.service.observe_usage([baseline], state, observed)
    api.service.observe_usage([{**baseline, "api_call_count": 4}], state, observed + 60)
    api.service.observe_usage([{**baseline, "api_call_count": 7}], state, observed + 120)
    record = next(iter(state["monthly_usage"]["2026-08"].values()))

    assert record["api_calls"] == 13
    assert state["unattributed"]["2026-08"][-1]["reason"] == "counter_reset"


def test_legacy_stores_and_historical_month_without_checkpoint_are_unavailable(plugin, monkeypatch):
    api, home = plugin
    state_path = api.service.store_path(home)
    state_path.parent.mkdir(parents=True)
    for version in (1, 2, None):
        legacy = {"providers": {"anthropic": {}}, "monthly_usage": {"2026-07": {"unsafe": {}}}}
        if version is not None:
            legacy["schema_version"] = version
        state_path.write_text(json.dumps(legacy))
        state = api.service.load_state(home)
        assert state["monthly_usage"] == {}
        assert state["legacy_baseline_unavailable"] is (version is not None)

    monkeypatch.setattr(api.service, "read_usage", lambda: ([], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))
    report = api.service.build_live_overview("2026-07", state, observed_at=datetime(2026, 8, 2, tzinfo=api.service.TORONTO).timestamp())
    assert report["coverage"]["status"] == "unavailable"


def test_breakdowns_aggregate_model_task_pairs_and_bound_baseline_gaps(plugin, monkeypatch):
    api, _ = plugin
    observed = datetime(2026, 8, 2, tzinfo=api.service.TORONTO).timestamp()
    state = api.service.default_state()
    state["monthly_usage"]["2026-08"] = {
        "one": {"provider": "anthropic", "profile": "one", "model": "claude", "task": "main", "api_calls": 2, "tokens": 3, "usage_cost_usd": 0, "cost_status": "estimated"},
        "two": {"provider": "anthropic", "profile": "two", "model": "claude", "task": "main", "api_calls": 5, "tokens": 7, "usage_cost_usd": 0, "cost_status": "estimated"},
        "three": {"provider": "anthropic", "profile": "one", "model": "claude", "task": "title", "api_calls": 1, "tokens": 1, "usage_cost_usd": 0, "cost_status": "estimated"},
    }
    monkeypatch.setattr(api.service, "read_usage", lambda: ([], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))

    report = api.service.build_live_overview("2026-08", state, observed_at=observed)
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")

    assert provider["models"] == [
        {"model": "claude", "task": "main", "api_calls": 7},
        {"model": "claude", "task": "title", "api_calls": 1},
    ]

    rows = [
        {**_row(1, provider=f"provider-{index}"), "session_id": str(index)}
        for index in range(api.service.MAX_UNATTRIBUTED_GAPS + 10)
    ]
    bounded_state = api.service.default_state()
    _, warnings = api.service.observe_usage(rows, bounded_state, observed)

    assert len(bounded_state["unattributed"]["2026-08"]) <= api.service.MAX_UNATTRIBUTED_GAPS
    assert len(warnings) <= api.service.MAX_UNATTRIBUTED_GAPS + 1


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


def test_manual_quota_accepts_fractional_percentages_via_settings_api(plugin):
    api, _ = plugin
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/provider-roi")

    response = TestClient(app).post(
        "/api/plugins/provider-roi/settings",
        json={"kind": "manual_quota", "provider": "anthropic", "used_percent": 41.5},
    )

    assert response.status_code == 200
    assert response.json()["state"]["manual_quotas"]["anthropic"]["used_percent"] == 41.5


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


def _minimal_plan(**overrides):
    plan = {
        "monthly_cost_cad": 100.0,
        "cost_status": "estimated",
        "renewal_on": None,
        "status": "active",
        "note": "",
    }
    plan.update(overrides)
    return plan


def _month_scoped_row(calls: int, estimated: float, actual: float, status: str, api, provider: str = "anthropic") -> dict[str, object]:
    """Build a usage row that classify_initial_baseline accepts as month_scoped for 2026-07."""
    july_start, _ = api.service.month_bounds("2026-07")
    return {**_row(calls, estimated, actual, status, provider=provider), "session_started_at": july_start + 60}


def _overview_with_usage(api, monkeypatch, *, calls, estimated, actual, status, classification="flat_rate", provider="anthropic"):
    rows = [_month_scoped_row(calls, estimated, actual, status, api, provider=provider)] if calls else []
    monkeypatch.setattr(api.service, "read_usage", lambda: (rows, []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))
    july = datetime(2026, 7, 2, tzinfo=api.service.TORONTO).timestamp()
    state = api.service.default_state()
    if rows:
        api.service.observe_usage(rows, state, july)
    state["providers"][provider] = {"classification": classification, "included": True}
    return api.service.build_live_overview("2026-07", state, observed_at=july)


def test_plan_configured_reflects_saved_plan_not_positive_amount(plugin, monkeypatch):
    """A plan explicitly configured at 0 CAD/month must still be plan_configured."""
    api, home = plugin
    api.service.update_state({"kind": "provider", "provider": "anthropic", "plan": _minimal_plan(monthly_cost_cad=0.0)}, home)
    monkeypatch.setattr(api.service, "read_usage", lambda: ([], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))
    report = api.service.build_live_overview("2026-08", api.service.load_state(home))
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert provider["plan_configured"] is True


def test_plan_not_configured_when_no_saved_plan(plugin, monkeypatch):
    api, _ = plugin
    monkeypatch.setattr(api.service, "read_usage", lambda: ([], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))
    state = api.service.default_state()
    state["providers"]["anthropic"] = {"classification": "flat_rate", "included": True}
    report = api.service.build_live_overview("2026-08", state)
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert provider["plan_configured"] is False


@pytest.mark.parametrize("plan", [
    {},
    {"monthly_cost_cad": None},
    {"monthly_cost_cad": True},
    {"monthly_cost_cad": "not-a-number"},
    {"monthly_cost_cad": float("nan")},
    {"monthly_cost_cad": float("inf")},
])
def test_plan_not_configured_without_finite_monthly_cad_cost(plugin, monkeypatch, plan):
    api, _ = plugin
    monkeypatch.setattr(api.service, "read_usage", lambda: ([], []))
    monkeypatch.setattr(api.service, "read_kanban", lambda: ({"delivered": 0, "approved": 0, "friction": 0}, []))
    state = api.service.default_state()
    state["providers"]["anthropic"] = {"classification": "flat_rate", "included": True, "plan": plan}

    report = api.service.build_live_overview("2026-08", state)
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")

    assert provider["plan_configured"] is False


def test_metered_cost_status_no_usage_data_when_no_activity(plugin, monkeypatch):
    api, _ = plugin
    report = _overview_with_usage(api, monkeypatch, calls=0, estimated=0, actual=0, status="estimated")
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert provider["metered_cost_status"] == "no_usage_data"


def test_metered_cost_status_flat_rate_not_applicable(plugin, monkeypatch):
    api, _ = plugin
    report = _overview_with_usage(api, monkeypatch, calls=10, estimated=0, actual=0, status="estimated", classification="flat_rate")
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert provider["activity"] == 10
    assert provider["usage_cost_usd"] == 0.0
    assert provider["metered_cost_status"] == "not_applicable_flat_rate"


def test_metered_cost_status_reported_with_actual_zero(plugin, monkeypatch):
    """PAYG with cost_status=actual and zero cost must still be reported (true zero)."""
    api, _ = plugin
    report = _overview_with_usage(api, monkeypatch, calls=10, estimated=0, actual=0, status="actual", classification="payg")
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert provider["metered_cost_status"] == "reported"
    assert provider["usage_cost_usd"] == 0.0


def test_metered_cost_status_not_reported_when_estimated_zero_payg(plugin, monkeypatch):
    """PAYG with activity but only estimated cost of zero must not masquerade as reported."""
    api, _ = plugin
    report = _overview_with_usage(api, monkeypatch, calls=10, estimated=0, actual=0, status="estimated", classification="payg")
    provider = next(item for item in report["providers"] if item["provider"] == "anthropic")
    assert provider["metered_cost_status"] == "not_reported"


def test_plan_update_accepts_monthly_cost_cad_only(plugin):
    api, _ = plugin
    update = api.PlanUpdate.model_validate(_minimal_plan())
    assert update.amount is None
    assert update.currency is None
    assert update.monthly_cost_cad == 100.0


def test_plan_update_accepts_complete_original_pair(plugin):
    api, _ = plugin
    update = api.PlanUpdate.model_validate(_minimal_plan(amount=80.0, currency="USD"))
    assert update.amount == 80.0
    assert update.currency == "USD"


@pytest.mark.parametrize("payload", [
    {**_minimal_plan(), "amount": 80.0},
    {**_minimal_plan(), "currency": "USD"},
])
def test_plan_update_rejects_half_original_pair(plugin, payload):
    api, _ = plugin
    with pytest.raises(ValueError, match="provided together"):
        api.PlanUpdate.model_validate(payload)


def test_settings_api_accepts_plan_with_only_monthly_cost_cad(plugin):
    api, _ = plugin
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/provider-roi")
    response = TestClient(app).post(
        "/api/plugins/provider-roi/settings",
        json={"kind": "provider", "provider": "anthropic", "plan": _minimal_plan()},
    )
    assert response.status_code == 200
    saved = response.json()["state"]["providers"]["anthropic"]["plan"]
    assert saved["monthly_cost_cad"] == 100.0
    assert "amount" not in saved
    assert "currency" not in saved


@pytest.mark.parametrize("plan", [
    {**_minimal_plan(), "amount": 80.0},
    {**_minimal_plan(), "currency": "USD"},
])
def test_settings_api_rejects_plan_with_half_original_pair(plugin, plan):
    api, _ = plugin
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/provider-roi")
    response = TestClient(app).post(
        "/api/plugins/provider-roi/settings",
        json={"kind": "provider", "provider": "anthropic", "plan": plan},
    )
    assert response.status_code == 422
