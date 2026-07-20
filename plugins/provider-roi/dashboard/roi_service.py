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
MAX_UNATTRIBUTED_GAPS = 64
COST_CAP = 1_000_000.0

DEFAULT_PROVIDER_RULES: dict[str, dict[str, Any]] = {
    "openrouter": {"classification": "payg", "included": False, "cancellable": False},
    "nous": {"classification": "critical", "included": True, "cancellable": False, "unique_role": True, "label": "Nous Portal"},
    "openai-codex": {"classification": "flat_rate", "included": True, "cancellable": True},
    "anthropic": {"classification": "flat_rate", "included": True, "cancellable": True},
    "kimi-coding": {"classification": "flat_rate", "included": True, "cancellable": True},
    "opencode-go": {"classification": "flat_rate", "included": True, "cancellable": True},
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


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _read_timestamp(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) and 0 < timestamp <= time.time() else None


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
                required = {"billing_provider", "model", "api_call_count"}
                if not required <= columns:
                    warnings.append(f"profile {profile}: usage table is incompatible")
                    continue
                session_columns = _table_columns(conn, "sessions")
                optional = lambda name, fallback: f"u.{name}" if name in columns else fallback
                session_id = optional("session_id", "u.rowid")
                session_join = ""
                session_started_at = "NULL"
                if "session_id" in columns and {"id", "started_at"} <= session_columns:
                    session_join = "LEFT JOIN sessions s ON s.id = u.session_id"
                    session_started_at = "s.started_at"
                query = f"""
                    SELECT {session_id} AS session_id, u.billing_provider, u.model,
                           {optional('task', "''")} AS task, u.api_call_count,
                           {optional('input_tokens', '0')} AS input_tokens,
                           {optional('output_tokens', '0')} AS output_tokens,
                           {optional('cache_read_tokens', '0')} AS cache_read_tokens,
                           {optional('cache_write_tokens', '0')} AS cache_write_tokens,
                           {optional('reasoning_tokens', '0')} AS reasoning_tokens,
                           {optional('estimated_cost_usd', '0')} AS estimated_cost_usd,
                           {optional('actual_cost_usd', '0')} AS actual_cost_usd,
                           {optional('cost_status', "''")} AS cost_status,
                           {optional('cost_source', "''")} AS cost_source,
                           {optional('first_seen', 'NULL')} AS first_seen,
                           {optional('last_seen', 'NULL')} AS last_seen,
                           {session_started_at} AS session_started_at
                    FROM session_model_usage u
                    {session_join}
                """
                for raw_row in conn.execute(query):
                    usage = {"profile": profile, **dict(raw_row)}
                    for timestamp_key in ("first_seen", "last_seen", "session_started_at"):
                        usage[timestamp_key] = _read_timestamp(usage[timestamp_key])
                    rows.append(usage)
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


_TOTAL_KEYS = (
    "api_call_count", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd",
)
_USAGE_KEYS = _TOTAL_KEYS[:6]


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _configured_plan_cost(plan: Any) -> float | None:
    if not isinstance(plan, dict) or "monthly_cost_cad" not in plan:
        return None
    return _finite_nonnegative(plan["monthly_cost_cad"])


def _timestamp(value: Any, observed_at: float) -> float | None:
    if value is None or value == "":
        return None
    timestamp = _finite_nonnegative(value)
    if timestamp is None or timestamp == 0 or timestamp > observed_at:
        return None
    return timestamp


def _identity(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(key) or "") for key in ("profile", "session_id", "billing_provider", "model", "task"))


def _totals(row: dict[str, Any]) -> dict[str, float] | None:
    totals: dict[str, float] = {}
    for key in _TOTAL_KEYS:
        value = _finite_nonnegative(row.get(key))
        if value is None:
            return None
        totals[key] = value
    return totals


def _token_total(totals: dict[str, float]) -> int:
    return int(sum(totals[key] for key in _USAGE_KEYS[1:]))


def _has_usage(totals: dict[str, float]) -> bool:
    return any(totals[key] > 0 for key in _USAGE_KEYS)


def _row_start(row: dict[str, Any], observed_at: float) -> float | None:
    return _timestamp(row.get("first_seen"), observed_at) or _timestamp(row.get("session_started_at"), observed_at)


def _timestamps_are_ordered(row: dict[str, Any], observed_at: float, row_start: float, last_seen: float | None) -> bool:
    if last_seen is None or row_start > last_seen:
        return False
    session_started_at = _timestamp(row.get("session_started_at"), observed_at)
    first_seen = _timestamp(row.get("first_seen"), observed_at)
    return not ((session_started_at and session_started_at > last_seen) or (first_seen and first_seen > last_seen))


def classify_initial_baseline(row: dict[str, Any], month: str, observed_at: float) -> dict[str, Any]:
    """Classify one identity's first observable baseline without attributing gaps."""
    totals = _totals(row)
    if totals is None:
        return {"state": "invalid_counter", "totals": None, "attributable_to_month": False}
    start, end = month_bounds(month)
    if not start <= observed_at < end:
        return {"state": "unavailable", "totals": totals, "attributable_to_month": False}
    row_start = _row_start(row, observed_at)
    if row_start is None:
        return {"state": "unknown_timestamp", "totals": totals, "attributable_to_month": False}
    last_seen = _timestamp(row.get("last_seen"), observed_at)
    if row.get("last_seen") not in (None, "") and last_seen is None:
        return {"state": "unknown_timestamp", "totals": totals, "row_start": row_start, "attributable_to_month": False}
    if last_seen is not None and not _timestamps_are_ordered(row, observed_at, row_start, last_seen):
        return {"state": "unknown_timestamp", "totals": totals, "row_start": row_start, "attributable_to_month": False}
    if start <= row_start < end:
        return {"state": "month_scoped", "totals": totals, "row_start": row_start, "attributable_to_month": True}
    if row_start >= end:
        return {"state": "unknown_timestamp", "totals": totals, "attributable_to_month": False}
    if last_seen is None:
        return {"state": "unknown_timestamp", "totals": totals, "row_start": row_start, "attributable_to_month": False}
    if last_seen < start:
        return {"state": "historical_inactive", "totals": totals, "row_start": row_start, "last_seen": last_seen, "attributable_to_month": False}
    if _has_usage(totals) and start <= last_seen < end:
        return {
            "state": "carry_in_cumulative", "totals": totals, "row_start": row_start,
            "last_seen": last_seen, "attributable_to_month": False,
            "semantics": "cumulative_at_baseline",
        }
    return {"state": "unknown_timestamp", "totals": totals, "row_start": row_start, "last_seen": last_seen, "attributable_to_month": False}


def _delta(current: dict[str, float], previous: dict[str, float]) -> dict[str, float] | None:
    values = {key: value - previous.get(key, 0.0) for key, value in current.items()}
    return values if all(value >= 0 for value in values.values()) else None


def _cost(delta: dict[str, float], status: str) -> tuple[float, str]:
    if status.strip().lower() in {"actual", "confirmed"}:
        return delta["actual_cost_usd"], "actual"
    return delta["estimated_cost_usd"], "estimated"


def _append_gap(unattributed: list[dict[str, Any]], *, provider: str, reason: str, coverage_start: Any, last_observed_at: float | None = None) -> bool:
    if any(gap.get("provider") == provider and gap.get("reason") == reason for gap in unattributed):
        return False
    if len(unattributed) >= MAX_UNATTRIBUTED_GAPS - 1:
        if len(unattributed) < MAX_UNATTRIBUTED_GAPS and not any(gap.get("reason") == "additional_gaps_omitted" for gap in unattributed):
            unattributed.append({"provider": "multiple", "reason": "additional_gaps_omitted"})
        return False
    gap = {"provider": provider, "reason": reason, "coverage_start": coverage_start}
    if last_observed_at is not None:
        gap["last_observed_at"] = last_observed_at
    unattributed.append(gap)
    return True


def _record_usage(monthly: dict[str, dict[str, Any]], identity: str, row: dict[str, Any], totals: dict[str, float], *, coverage_start: Any, observed_at: float) -> None:
    provider = str(row.get("billing_provider") or "unknown").strip().lower() or "unknown"
    record = monthly.setdefault(identity, {"provider": provider, "profile": row["profile"], "model": row.get("model") or "unknown", "task": row.get("task") or "", "api_calls": 0, "tokens": 0, "usage_cost_usd": 0.0, "cost_status": "estimated", "coverage_start": coverage_start, "last_observed_at": observed_at, "coverage": "partial"})
    record["api_calls"] += int(totals["api_call_count"])
    record["tokens"] += _token_total(totals)
    cost, status = _cost(totals, str(row.get("cost_status") or ""))
    record["usage_cost_usd"] += cost
    record["cost_status"] = "actual" if status == "actual" else record["cost_status"]
    record["last_observed_at"] = observed_at


def _baseline_record(observation: dict[str, Any], month: str, classification: dict[str, Any], observed_at: float) -> dict[str, Any]:
    months = observation.setdefault("months", {})
    existing = months.get(month)
    if isinstance(existing, dict):
        return existing
    record = {key: value for key, value in classification.items() if key != "totals"}
    record["baseline_totals"] = classification.get("totals")
    record["observed_at"] = observed_at
    months[month] = record
    return record


def observe_usage(rows: list[dict[str, Any]], state: dict[str, Any], observed_at: float | None = None) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    observed_at = observed_at or time.time()
    observed_month = datetime.fromtimestamp(observed_at, TORONTO).strftime("%Y-%m")
    monthly = state.setdefault("monthly_usage", {}).setdefault(observed_month, {})
    unattributed = state.setdefault("unattributed", {}).setdefault(observed_month, [])
    observations = state.setdefault("observations", {})
    legacy_store = bool(state.get("legacy_baseline_unavailable"))
    warning_keys: set[tuple[str, str]] = set()
    for row in rows:
        identity, provider = _identity(row), str(row.get("billing_provider") or "unknown").strip().lower() or "unknown"
        totals = _totals(row)
        previous = observations.get(identity)
        observation = previous if isinstance(previous, dict) else {}
        has_metadata = isinstance(observation.get("months"), dict)
        if legacy_store or not previous or not has_metadata:
            classification = classify_initial_baseline(row, observed_month, observed_at)
            if legacy_store or (previous and not has_metadata):
                classification = {"state": "legacy_baseline_unavailable", "totals": totals, "attributable_to_month": False}
            baseline = _baseline_record(observation, observed_month, classification, observed_at)
            if baseline["state"] == "month_scoped" and totals is not None:
                _record_usage(monthly, identity, row, totals, coverage_start=observed_at, observed_at=observed_at)
            if _append_gap(unattributed, provider=provider, reason=baseline["state"], coverage_start=observed_at):
                warning_keys.add((provider, baseline["state"]))
        elif totals is None:
            _append_gap(unattributed, provider=provider, reason="invalid_counter", coverage_start=observation.get("coverage_start", observed_at), last_observed_at=observed_at)
            warning_keys.add((provider, "invalid_counter"))
        else:
            previous_totals = observation.get("totals")
            delta = _delta(totals, previous_totals) if isinstance(previous_totals, dict) else None
            if observation.get("month") != observed_month:
                _baseline_record(observation, observed_month, {"state": "cross_boundary_unavailable", "totals": totals, "attributable_to_month": False}, observed_at)
                if _append_gap(unattributed, provider=provider, reason="coverage_gap", coverage_start=observation.get("coverage_start"), last_observed_at=observed_at):
                    warning_keys.add((provider, "coverage_gap"))
            elif delta is None:
                _baseline_record(observation, observed_month, {"state": "counter_reset", "totals": totals, "attributable_to_month": False}, observed_at)
                if _append_gap(unattributed, provider=provider, reason="counter_reset", coverage_start=observation.get("coverage_start"), last_observed_at=observed_at):
                    warning_keys.add((provider, "counter_reset"))
            elif any(delta.values()):
                _record_usage(monthly, identity, row, delta, coverage_start=observation.get("coverage_start", observed_at), observed_at=observed_at)
        observation.update({"identity": identity, "totals": totals, "coverage_start": observation.get("coverage_start", observed_at), "last_observed_at": observed_at, "month": observed_month})
        observations[identity] = observation
    if rows:
        state["legacy_baseline_unavailable"] = False
    warnings = [f"{provider}: usage unattributed ({reason})" for provider, reason in sorted(warning_keys)]
    return {month: list(items.values()) for month, items in state.get("monthly_usage", {}).items()}, warnings


def _carry_ins(state: dict[str, Any], month: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for observation in state.get("observations", {}).values():
        if not isinstance(observation, dict):
            continue
        record = observation.get("months", {}).get(month)
        if not isinstance(record, dict) or record.get("state") != "carry_in_cumulative":
            continue
        totals = record.get("baseline_totals")
        if not isinstance(totals, dict) or any(_finite_nonnegative(totals.get(key)) is None for key in _TOTAL_KEYS):
            continue
        identity = str(observation.get("identity") or "")
        provider = identity.split("|")[2] if identity.count("|") >= 2 else "unknown"
        bucket = buckets.setdefault(provider or "unknown", {"api_calls": 0, "tokens": 0, "rows": 0})
        bucket["api_calls"] += int(totals["api_call_count"])
        bucket["tokens"] += _token_total(totals)
        bucket["rows"] += 1
    return [
        {"provider": provider, **bucket, "semantics": "cumulative_at_baseline", "attributable_to_month": False}
        for provider, bucket in sorted(buckets.items())
    ]


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
        item = grouped.setdefault(provider, {"provider": provider, "model_calls": defaultdict(int), "profiles": set(), "activity": 0, "tokens": 0, "usage_cost_usd": 0.0, "cost_status": "estimated", "coverage": "partial"})
        item["activity"] += int(row["api_calls"]); item["tokens"] += int(row["tokens"]); item["usage_cost_usd"] += _number(row["usage_cost_usd"])
        item["cost_status"] = "actual" if row.get("cost_status") == "actual" else item["cost_status"]
        item["profiles"].add(row["profile"])
        item["model_calls"][(str(row["model"]), str(row["task"]))] += int(row["api_calls"])
    configured = set(state.get("providers", {}))
    detected = {str(row.get("billing_provider") or "unknown").strip().lower() or "unknown" for row in usage_rows}
    for provider in configured | detected | set(grouped):
        item = grouped.setdefault(provider, {"provider": provider, "model_calls": defaultdict(int), "profiles": set(), "activity": 0, "tokens": 0, "usage_cost_usd": 0.0, "cost_status": "estimated", "coverage": "partial"})
        item["profiles"] = sorted(item["profiles"])
        item["models"] = [
            {"model": model, "task": task, "api_calls": calls}
            for (model, task), calls in sorted(item.pop("model_calls").items())
        ]
        item["rule"] = provider_rule(provider, state)
        item["manual_quota"] = state.get("manual_quotas", {}).get(provider)
        item["exception"] = state.get("exceptions", {}).get(provider)
        plan = item["rule"].get("plan")
        item["plan_configured"] = _configured_plan_cost(plan) is not None
        if item["activity"] == 0:
            item["metered_cost_status"] = "no_usage_data"
        elif item["rule"]["classification"] == "flat_rate" and item["usage_cost_usd"] == 0:
            item["metered_cost_status"] = "not_applicable_flat_rate"
        elif item["usage_cost_usd"] > 0 or item["cost_status"] == "actual":
            item["metered_cost_status"] = "reported"
        else:
            item["metered_cost_status"] = "not_reported"
        # There is no task→provider relation in Kanban's durable schema. Keep
        # global totals once in summary and never copy them into every provider.
        item["delivered"] = 0
        item["approved"] = 0
        item["friction"] = 0
        item["low_value"] = is_low_value(item)
        item["verdict"] = verdict(item, state, month)
    providers = [grouped[key] for key in sorted(grouped)]
    gaps = state.get("unattributed", {}).get(month, [])
    carry_ins = _carry_ins(state, month)
    carry_in_total = sum(item["api_calls"] for item in carry_ins)
    observed_month = datetime.fromtimestamp(observed_at or time.time(), TORONTO).strftime("%Y-%m")
    historical_unavailable = month < observed_month and month not in state.get("snapshots", {})
    coverage_status = "unavailable" if historical_unavailable else "partial" if month == TRANSITION_MONTH or gaps else "complete"
    if historical_unavailable:
        warnings.append("historical month unavailable without a persisted checkpoint")
    elif gaps:
        warnings.append("partial coverage: unattributed usage is excluded from monthly activity, cost, and verdicts")
    return {
        "month": month,
        "generated_at": int(observed_at or time.time()),
        "providers": providers,
        "summary": {"activity": sum(item["activity"] for item in providers), "carry_in_activity": carry_in_total, "carry_in_semantics": "cumulative_at_baseline; not monthly activity", **kanban},
        "global_kanban": kanban,
        "coverage": {"status": coverage_status, "unattributed": gaps, "carry_ins": carry_ins},
        "warnings": warnings,
        "provenance": {
            "activity": "month-scoped baselines and strictly intra-month session_model_usage deltas",
            "carry_ins": "carry-in counters are cumulative-at-baseline only; excluded from monthly activity, cost, breakdowns, and verdicts",
            "kanban": "global only; task/provider attribution unavailable",
        },
    }


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
