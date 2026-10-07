"""Offline contract for GPT-Live voice instructions and client delegation."""

import json

import pytest

from tools import voice_live

# The original default instruction payload, frozen before splitting style from protocol.
DEFAULT_INSTRUCTIONS = (
    "You are Hermes, a calm and friendly voice assistant. Speak naturally at an unhurried pace. "
    "Be clear and direct, not overly cheerful. If the user is frustrated, acknowledge it briefly "
    "and focus on the next helpful step.\n\n"
    "Backchannel policy: Use moderate backchannels. Acknowledge naturally without competing with "
    "the main response.\n\n"
    "Interruption policy: Stop speaking when the user interrupts. Listen to what they say.\n\n"
    "Delegation policy:\n"
    "Backend tools:\n"
    "- Hermes agent: a full AI agent with tools — it can run commands, read and edit files, "
    "browse the web, search, remember things across sessions, schedule tasks, and reason "
    "carefully about anything. It is the one who actually does work and knows facts.\n\n"
    "Delegate to the backend when:\n"
    "- The user asks a question that needs facts, current information, or careful reasoning.\n"
    "- The user asks you to do, check, find, make, fix, run or remember anything.\n"
    "- A correction changes work already requested.\n\n"
    "Do not delegate to the backend when:\n"
    "- The user greets you, makes small talk, or asks you to repeat a result already provided.\n"
    "- You need a brief clarification to understand the request.\n\n"
    "Delegate before giving an answer that depends on backend work. Do not guess the result "
    "while waiting; say briefly that you are checking, then wait for the result."
)
PROTOCOL = DEFAULT_INSTRUCTIONS[DEFAULT_INSTRUCTIONS.index("Backchannel policy:"):]


def test_default_session_payload_preserves_original_bytes(monkeypatch):
    monkeypatch.setattr(voice_live, "_live_section", lambda voice=None: {})
    config = voice_live.build_session_config()

    assert json.dumps(config, ensure_ascii=False).encode("utf-8") == json.dumps({
        "model": "gpt-live-1",
        "instructions": DEFAULT_INSTRUCTIONS,
        "audio": {"output": {"voice": "marin"}},
        "delegation": {"type": "client"},
    }, ensure_ascii=False).encode("utf-8")
    assert "Delegate to the backend when:" in config["instructions"]


@pytest.mark.parametrize("instructions", ["Speak Spanish.", "  Speak Spanish.  "])
def test_configured_style_replaces_default_but_preserves_protocol(monkeypatch, instructions):
    monkeypatch.setattr(voice_live, "_live_section", lambda voice=None: {"instructions": instructions})
    config = voice_live.build_session_config()

    assert config["instructions"] == "Speak Spanish.\n\n" + PROTOCOL
    assert "calm and friendly voice assistant" not in config["instructions"]
    assert "Delegate to the backend when:" in config["instructions"]
    assert config["delegation"] == {"type": "client"}
