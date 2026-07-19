"""Provider ROI dashboard routes, mounted at /api/plugins/provider-roi/."""
from __future__ import annotations

from dataclasses import asdict
import importlib.util
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_service_path = Path(__file__).with_name("roi_service.py")
_spec = importlib.util.spec_from_file_location("provider_roi_service", _service_path)
if _spec is None or _spec.loader is None:  # pragma: no cover - installation failure
    raise RuntimeError("Provider ROI service is unavailable")
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)

router = APIRouter()


class StoreUpdate(BaseModel):
    kind: str
    provider: str
    classification: str | None = None
    included: bool | None = None
    cancellable: bool | None = None
    monthly_cost_usd: float | None = None
    unique_role: bool | None = None
    label: str | None = None
    reason: str | None = None
    expires_on: str | None = None
    used_percent: float | None = None


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
        state = service.update_state(update.model_dump(exclude_none=True))
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
