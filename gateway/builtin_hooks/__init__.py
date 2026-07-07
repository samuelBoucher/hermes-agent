"""Built-in gateway hooks that are always registered."""

from __future__ import annotations

from gateway.builtin_hooks.dashboard_autostart import handle as dashboard_autostart_handle


def register_builtin_hooks(registry) -> None:
    """Register shipped gateway hooks that should always be active."""
    if any(h.get("name") == "dashboard-autostart" for h in registry._loaded_hooks):
        return
    registry._handlers.setdefault("gateway:startup", []).append(
        dashboard_autostart_handle
    )
    registry._loaded_hooks.append(
        {
            "name": "dashboard-autostart",
            "description": "Start the Hermes dashboard automatically on gateway boot.",
            "events": ["gateway:startup"],
            "path": "<builtin>",
        }
    )
