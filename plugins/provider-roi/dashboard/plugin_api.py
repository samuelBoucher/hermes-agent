"""Provider ROI dashboard routes, mounted at /api/plugins/provider-roi/."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import importlib.util
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

_service_path = Path(__file__).with_name("roi_service.py")
_spec = importlib.util.spec_from_file_location("provider_roi_service", _service_path)
if _spec is None or _spec.loader is None:  # pragma: no cover - installation failure
    raise RuntimeError("Provider ROI service is unavailable")
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)

router = APIRouter()
MAX_TEXT = 500


class PlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    monthly_cost_cad: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    cost_status: Literal["estimated", "confirmed"]
    renewal_on: date | None = None
    status: Literal["active", "trial", "cancellation-planned"]
    note: str = Field(default="", max_length=MAX_TEXT)


class StoreUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["provider", "exception", "manual_quota"]
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    classification: Literal["flat_rate", "payg", "critical", "unclassified"] | None = None
    included: bool | None = None
    cancellable: bool | None = None
    unique_role: bool | None = None
    label: str | None = Field(default=None, max_length=MAX_TEXT)
    plan: PlanUpdate | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT)
    expires_on: date | None = None
    used_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_kind_fields(self) -> "StoreUpdate":
        if self.kind == "exception" and (self.reason is None or self.expires_on is None):
            raise ValueError("exception requires reason and expires_on")
        if self.kind == "manual_quota" and self.used_percent is None:
            raise ValueError("manual_quota requires used_percent")
        return self


@router.get("/overview")
def get_overview(month: str | None = None) -> dict[str, Any]:
    try:
        return service.overview(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    state = service.load_state()
    return {
        "providers": state["providers"],
        "exceptions": state["exceptions"],
        "manual_quotas": state["manual_quotas"],
        "store_path": str(service.store_path()),
        "profile_switcher": "Each Hermes profile has its own private Provider ROI store.",
    }


@router.post("/settings")
def post_settings(update: StoreUpdate) -> dict[str, Any]:
    try:
        state = service.update_state(update.model_dump(mode="json", exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"saved": True, "state": state}


@router.get("/quota/{provider}")
def get_native_quota(provider: str) -> dict[str, Any]:
    try:
        provider = service.validate_provider_id(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if provider not in {"openai-codex", "anthropic", "openrouter"}:
        raise HTTPException(status_code=404, detail="No stable native quota API for this provider; use manual quota.")
    from agent.account_usage import fetch_account_usage
    snapshot = fetch_account_usage(provider)
    if snapshot is None:
        return {"provider": provider, "available": False}
    return {
        "provider": provider,
        "available": snapshot.available,
        "source": snapshot.source,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "plan": snapshot.plan,
        "details": list(snapshot.details),
        "windows": [asdict(window) for window in snapshot.windows],
    }
