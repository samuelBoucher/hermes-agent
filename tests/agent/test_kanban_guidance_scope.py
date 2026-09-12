"""Kanban tools grant board access; they do not imply an assigned worker task."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent.agent_init import _load_tools
from agent.delegation_context import (
    delegated_child_context,
    non_dispatcher_owned_context,
)
from agent.prompt_builder import KANBAN_GUIDANCE
from agent.system_prompt import _tool_guidance_block
from hermes_constants import get_hermes_home


@pytest.mark.parametrize(
    "role", ["orchestrator", "worker", "cron", "delegate", "delegate-process"]
)
def test_worker_guidance_follows_task_ownership(role, monkeypatch):
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("toolsets: [kanban]\nplugins:\n  enabled: []\n")
    monkeypatch.delenv("HERMES_DELEGATED_CHILD_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    if role != "orchestrator":
        monkeypatch.setenv("HERMES_KANBAN_TASK", "test-task")
    if role == "delegate-process":
        monkeypatch.setenv("HERMES_DELEGATED_CHILD_CONTEXT", "1")
    context = {
        "cron": non_dispatcher_owned_context,
        "delegate": delegated_child_context,
    }.get(role, nullcontext)
    with context():
        agent = SimpleNamespace(quiet_mode=True)
        _load_tools(agent, ["kanban"], None)
        if role in {"orchestrator", "cron"}:
            assert {"kanban_show", "kanban_list"} <= agent.valid_tool_names
        expected = role == "worker"
        assert (KANBAN_GUIDANCE in (_tool_guidance_block(agent) or "")) == expected
        # Prompt-only builders must enforce the same rule even with a tool snapshot
        # inherited from a different execution context.
        fallback = SimpleNamespace(valid_tool_names={"kanban_show", "kanban_list"})
        assert (KANBAN_GUIDANCE in (_tool_guidance_block(fallback) or "")) == expected


@pytest.mark.parametrize("worker_at_start", [False, True])
def test_guidance_snapshot_survives_ambient_task_changes(worker_at_start, monkeypatch):
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("toolsets: [kanban]\nplugins:\n  enabled: []\n")
    monkeypatch.delenv("HERMES_DELEGATED_CHILD_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    if worker_at_start:
        monkeypatch.setenv("HERMES_KANBAN_TASK", "test-task")
    agent = SimpleNamespace(quiet_mode=True)
    _load_tools(agent, ["kanban"], None)
    before = _tool_guidance_block(agent)
    assert (KANBAN_GUIDANCE in (before or "")) == worker_at_start
    if worker_at_start:
        monkeypatch.delenv("HERMES_KANBAN_TASK")
    else:
        monkeypatch.setenv("HERMES_KANBAN_TASK", "another-task")
    assert _tool_guidance_block(agent) == before
