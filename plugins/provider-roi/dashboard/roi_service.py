"""Read-only data and private-state service for the Provider ROI dashboard."""
from __future__ import annotations

import importlib.util
import math
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from hermes_cli import kanban_db
from hermes_cli import kanban_diagnostics as diagnostics

_store_path = Path(__file__).with_name("roi_store.py")
_store_spec = importlib.util.spec_from_file_location("provider_roi_store", _store_path)
if _store_spec is None or _store_spec.loader is None:  # pragma: no cover
    raise RuntimeError("Provider ROI store is unavailable")
store = importlib.util.module_from_spec(_store_spec)
sys.modules[_store_spec.name] = store
_store_spec.loader.exec_module(store)

TORONTO = ZoneInfo("America/Toronto")
TRANSITION_MONTH = "2026-07"
MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_SOURCES = 64
COST_CAP = 1_000_000.0

DEFAULT_PROVIDER_RULES: dict[str, dict[str, Any]] = {
    "openrouter": {"classification": "payg", "included": False, "cancellable": False},
    "nous": {"classification": "critical", "included": True, "cancellable": False, "unique_role": True, "label": "Nous Portal"},
    "openai-codex": {"classification": "flat_rate", "included": True, "cancellable": True},
    "anthropic": {"classification": "flat_rate", "included": True, "cancellable": True},
    "kimi-coding": {"classification": "flat_rate", "included": True, "cancellable": True},
    "opencode-zen": {"classification": "flat_rate", "included": True, "cancellable": True},
}
IMMUTABLE_RULES = {"openrouter", "nous"}

# Backward-compatible public store helpers for the dashboard API and tests.
store_path = store.store_path
default_state = store.default_state
load_state = store.load_state
save_state = store.save_state


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
    year, mon = (int(part) for part in month.split("-"))
    datetime(year, mon, 1)  # calendar validation, not merely a regex
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
    current = home or store_path().parents[2]
    found: dict[Path, str] = {current.resolve(): "active"}
    try:
        from hermes_cli.profiles import list_profiles
        for profile in list_profiles():
            found[Path(profile.path).resolve()] = profile.name
    except Exception:
        pass
    return sorted(((name, path) for path, name in found.items()), key=lambda item: item[0])


def _readonly(path: Path) -> sqlite3.Connection:
    # as_uri percent-encodes spaces, '#' and '?' before SQLite receives mode=ro.
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def _usage_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in conn.execute("PRAGMA table_info(session_model_usage)")}


def read_usage(homes: Iterable[tuple[str, Path]] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
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
                columns = _usage_columns(conn)
                required = {"billing_provider", "model", "task", "api_call_count"}
                if not required <= columns:
                    warnings.append(f"profile {profile}: usage table is incompatible")
                    continue
                optional = lambda name, fallback: name if name in columns else fallback
                query = f"""
                    SELECT {optional('session_id', 'rowid')} AS session_id, billing_provider, model, task,
                           api_call_count, {optional('input_tokens', '0')} AS input_tokens,
                           {optional('output_tokens', '0')} AS output_tokens,
                           {optional('cache_read_tokens', '0')} AS cache_read_tokens,
                           {optional('cache_write_tokens', '0')} AS cache_write_tokens,
                           {optional('reasoning_tokens', '0')} AS reasoning_tokens,
                           {optional('estimated_cost_usd', '0')} AS estimated_cost_usd,
                           {optional('actual_cost_usd', '0')} AS actual_cost_usd,
                           {optional('cost_status', "''")} AS cost_status,
                           {optional('cost_source', "''")} AS cost_source
                    FROM session_model_usage
                """
                rows.extend({"profile": profile, **dict(row)} for row in conn.execute(query))
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError) as exc:
            warnings.append(f"profile {profile}: usage unavailable ({exc})")
    return rows, warnings


def _board_db_path(board: str) -> Path:
    return kanban_db.kanban_home() / "kanban.db" if board == kanban_db.DEFAULT_BOARD else kanban_db.board_dir(board) / "kanban.db"


def read_kanban() -> tuple[dict[str, int], list[str]]:
    totals = {"delivered": 0, "approved": 0, "friction": 0}
    warnings: list[str] = []
    try:
        boards = kanban_db.list_boards(include_archived=False)
    except Exception as exc:
        return totals, [f"boards unavailable ({exc})"]
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
                events, runs = defaultdict(list), defaultdict(list)
                for row in conn.execute("SELECT * FROM task_events ORDER BY id"): events[row["task_id"]].append(row)
                for row in conn.execute("SELECT * FROM task_runs ORDER BY id"): runs[row["task_id"]].append(row)
                for task in tasks:
                    totals["friction"] += sum(item.count for item in diagnostics.compute_task_diagnostics(task, events[task["id"]], runs[task["id"]]))
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError) as exc:
            warnings.append(f"board {board}: diagnostics unavailable ({exc})")
    return totals, warnings


def _number(value: Any) -> float:
    value = float(value or 0)
    if not math.isfinite(value):
        return 0.0
    return value


def _identity(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(key) or "") for key in ("profile", "session_id", "billing_provider", "model", "task"))


def _totals(row: dict[str, Any]) -> dict[str, float]:
    return {key: _number(row.get(key)) for key in (
        "api_call_count", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd",
    )}


def _delta(current: dict[str, float], previous: dict[str, float]) -> dict[str, float] | None:
    values = {key: value - previous.get(key, 0.0) for key, value in current.items()}
    return values if all(value >= 0 for value in values.values()) else None


def _cost(delta: dict[str, float], status: str) -> tuple[float, str]:
    # SQLite's NOT NULL actual column defaults to 0; only explicit confirmation
    # may make it authoritative.
    if status.strip().lower() in {"actual", "confirmed"}:
        return delta["actual_cost_usd"], "actual"
    return delta["estimated_cost_usd"], "estimated"


def observe_usage(rows: list[dict[str, Any]], state: dict[str, Any], observed_at: float | None = None) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    observed_at = observed_at or time.time()
    observed_month = datetime.fromtimestamp(observed_at, TORONTO).strftime("%Y-%m")
    monthly = state.setdefault("monthly_usage", {}).setdefault(observed_month, {})
    unattributed = state.setdefault("unattributed", {}).setdefault(observed_month, [])
    observations = state.setdefault("observations", {})
    warnings: list[str] = []
    for row in rows:
        identity, totals = _identity(row), _totals(row)
        provider = str(row.get("billing_provider") or "unknown").strip().lower() or "unknown"
        previous = observations.get(identity)
        observations[identity] = {"totals": totals, "coverage_start": (previous or {}).get("coverage_start", observed_at), "last_observed_at": observed_at, "month": observed_month}
        if not previous:
            unattributed.append({"provider": provider, "reason": "initial_baseline", "coverage_start": observed_at})
            warnings.append(f"{provider}: partial coverage begins at first observation")
            continue
        delta = _delta(totals, previous.get("totals", {}))
        if delta is None or previous.get("month") != observed_month:
            unattributed.append({"provider": provider, "reason": "coverage_gap" if delta else "counter_reset", "coverage_start": previous.get("coverage_start"), "last_observed_at": observed_at})
            warnings.append(f"{provider}: delta unattributed due to coverage gap")
            continue
        record = monthly.setdefault(identity, {"provider": provider, "profile": row["profile"], "model": row.get("model") or "unknown", "task": row.get("task") or "main", "api_calls": 0, "tokens": 0, "usage_cost_usd": 0.0, "cost_status": "estimated", "coverage_start": previous.get("coverage_start"), "last_observed_at": observed_at, "coverage": "partial"})
        record["api_calls"] += int(delta["api_call_count"])
        record["tokens"] += int(sum(delta[key] for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")))
        cost, status = _cost(delta, str(row.get("cost_status") or ""))
        record["usage_cost_usd"] += cost
        record["cost_status"] = "actual" if status == "actual" else record["cost_status"]
        record["last_observed_at"] = observed_at
    return {month: list(items.values()) for month, items in state.get("monthly_usage", {}).items()}, warnings


def provider_rule(provider: str, state: dict[str, Any]) -> dict[str, Any]:
    rule = dict(DEFAULT_PROVIDER_RULES.get(provider, {"classification": "unclassified", "included": False, "cancellable": False}))
    custom = state.get("providers", {}).get(provider, {})
    if isinstance(custom, dict) and provider not in IMMUTABLE_RULES:
        rule.update({key: custom[key] for key in ("classification", "included", "cancellable", "unique_role", "label", "plan") if key in custom})
    if provider == "openrouter":
        rule.update(DEFAULT_PROVIDER_RULES[provider])
    elif provider == "nous":
        rule.update(DEFAULT_PROVIDER_RULES[provider])
    return rule


def is_low_value(item: dict[str, Any]) -> bool:
    return item["activity"] < 5 and item["delivered"] == 0 and item["approved"] == 0


def verdict(item: dict[str, Any], state: dict[str, Any], month: str) -> dict[str, str]:
    rule, provider = item["rule"], item["provider"]
    exception = state.get("exceptions", {}).get(provider)
    if isinstance(exception, dict) and exception.get("reason") and exception.get("expires_on", "") >= datetime.now(TORONTO).date().isoformat():
        return {"action": "keep", "reason": "active exception"}
    if rule["classification"] == "payg" or not rule.get("included"):
        return {"action": "exclude", "reason": "PAYG or unclassified provider is outside ROI"}
    if rule["classification"] == "critical":
        return {"action": "keep", "reason": "critical infrastructure; never auto-cancel"}
    if item.get("friction", 0) > max(2, item["activity"] // 10):
        return {"action": "reassign", "reason": "provider-attributed Kanban friction exceeds activity"}
    partial = item.get("coverage") != "complete" or month == TRANSITION_MONTH
    if is_low_value(item):
        prior = next((candidate for candidate in state.get("snapshots", {}).get(previous_month(month), {}).get("providers", []) if candidate.get("provider") == provider), {})
        if prior.get("low_value") and not rule.get("unique_role") and rule.get("cancellable") and not partial:
            return {"action": "cancel", "reason": "two complete low months and no unique role"}
        return {"action": "observe", "reason": "partial coverage or first low month"}
    plan = rule.get("plan") if isinstance(rule.get("plan"), dict) else {}
    if _number(plan.get("monthly_cost_cad")) > 0 and item["activity"] < 20 and not partial:
        return {"action": "downgrade", "reason": "low activity versus configured fixed cost"}
    return {"action": "keep", "reason": "activity and delivery justify retention"}


def build_live_overview(month: str, state: dict[str, Any], *, observed_at: float | None = None) -> dict[str, Any]:
    usage_rows, warnings = read_usage()
    _, observation_warnings = observe_usage(usage_rows, state, observed_at)
    warnings.extend(observation_warnings)
    kanban, kanban_warnings = read_kanban()
    warnings.extend(kanban_warnings)
    monthly = state.get("monthly_usage", {}).get(month, {})
    grouped: dict[str, dict[str, Any]] = {}
    for row in monthly.values() if isinstance(monthly, dict) else []:
        provider = str(row["provider"])
        item = grouped.setdefault(provider, {"provider": provider, "models": [], "profiles": set(), "activity": 0, "tokens": 0, "usage_cost_usd": 0.0, "cost_status": "estimated", "coverage": "partial"})
        item["activity"] += int(row["api_calls"]); item["tokens"] += int(row["tokens"]); item["usage_cost_usd"] += _number(row["usage_cost_usd"])
        item["cost_status"] = "actual" if row.get("cost_status") == "actual" else item["cost_status"]
        item["profiles"].add(row["profile"])
        item["models"].append({key: row[key] for key in ("model", "task", "api_calls")})
    configured = set(state.get("providers", {}))
    detected = {str(row.get("billing_provider") or "unknown").strip().lower() or "unknown" for row in usage_rows}
    for provider in configured | detected | set(grouped):
        item = grouped.setdefault(provider, {"provider": provider, "models": [], "profiles": set(), "activity": 0, "tokens": 0, "usage_cost_usd": 0.0, "cost_status": "estimated", "coverage": "partial"})
        item["profiles"] = sorted(item["profiles"])
        item["rule"] = provider_rule(provider, state)
        item["manual_quota"] = state.get("manual_quotas", {}).get(provider)
        item["exception"] = state.get("exceptions", {}).get(provider)
        # There is no task→provider relation in Kanban's durable schema. Keep
        # global totals once in summary and never copy them into every provider.
        item["delivered"] = 0
        item["approved"] = 0
        item["friction"] = 0
        item["low_value"] = is_low_value(item)
        item["verdict"] = verdict(item, state, month)
    providers = [grouped[key] for key in sorted(grouped)]
    gaps = state.get("unattributed", {}).get(month, [])
    if gaps:
        warnings.append("coverage gap: unattributed deltas are excluded from provider verdicts")
    return {"month": month, "generated_at": int(observed_at or time.time()), "providers": providers, "summary": {"activity": sum(item["activity"] for item in providers), **kanban}, "global_kanban": kanban, "coverage": {"status": "partial" if month == TRANSITION_MONTH or gaps else "complete", "unattributed": gaps}, "warnings": warnings, "provenance": {"activity": "observed session_model_usage deltas", "kanban": "global only; task/provider attribution unavailable"}}


def overview(month: str | None = None, *, now: datetime | None = None, home: Path | None = None) -> dict[str, Any]:
    current, requested = validate_month(None, now), validate_month(month, now)
    with store.state_transaction(home) as state:
        closed = requested < current and requested >= TRANSITION_MONTH
        snapshot = state["snapshots"].get(requested)
        if closed and isinstance(snapshot, dict):
            return {**snapshot, "snapshot": True, "store_path": str(store_path(home))}
        live = build_live_overview(requested, state, observed_at=(now.timestamp() if now else None))
        if closed:
            state["snapshots"][requested] = live
        return {**live, "snapshot": False, "store_path": str(store_path(home))}


def update_state(payload: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    provider, kind = validate_provider_id(payload.get("provider", "")), payload.get("kind")
    with store.state_transaction(home) as state:
        if kind == "provider":
            if provider in IMMUTABLE_RULES:
                raise ValueError(f"{provider} has immutable Provider ROI safeguards")
            values = {key: payload[key] for key in ("classification", "included", "cancellable", "unique_role", "label", "plan") if key in payload}
            state["providers"][provider] = {**state["providers"].get(provider, {}), **values}
        elif kind == "exception":
            state["exceptions"][provider] = {"reason": payload["reason"], "expires_on": payload["expires_on"]}
        elif kind == "manual_quota":
            state["manual_quotas"][provider] = {"used_percent": payload["used_percent"], "recorded_at": int(time.time())}
        else:
            raise ValueError("kind must be provider, exception, or manual_quota")
        return state
