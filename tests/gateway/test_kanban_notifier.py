import asyncio
from pathlib import Path

import pytest

from gateway.config import Platform
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb


class RecordingAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append({"chat_id": chat_id, "text": text, "metadata": metadata or {}})


class RecordingDiscordAdapter(RecordingAdapter):
    def __init__(self):
        super().__init__()
        self.created_threads = []

    async def create_kanban_notification_thread(self, parent_chat_id, *, task_id, title, board=None):
        thread_id = f"thread-{task_id}"
        self.created_threads.append({
            "parent_chat_id": parent_chat_id,
            "task_id": task_id,
            "title": title,
            "board": board,
            "thread_id": thread_id,
        })
        return thread_id


class DisconnectedAdapters(dict):
    """Expose a platform during collection, then simulate disconnect on get()."""

    def get(self, key, default=None):
        return None


async def _run_one_notifier_tick(monkeypatch, runner):
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        if delay == 5:
            return None
        runner._running = False
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await runner._kanban_notifier_watcher(interval=1)


def _make_runner(adapter):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_sub_fail_counts = {}
    return runner


def _make_discord_runner(adapter):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = {Platform.DISCORD: adapter}
    runner._kanban_sub_fail_counts = {}
    return runner


def _create_completed_subscription(summary="done once"):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="notify once", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        kb.complete_task(conn, tid, summary=summary)
        return tid
    finally:
        conn.close()


def _unseen_terminal_events(tid):
    conn = kb.connect()
    try:
        _, events = kb.unseen_events_for_sub(
            conn,
            task_id=tid,
            platform="telegram",
            chat_id="chat-1",
            kinds=["completed", "blocked", "gave_up", "crashed", "timed_out"],
        )
        return events
    finally:
        conn.close()


def test_kanban_notifier_dedupes_board_slugs_pointing_to_same_db(tmp_path, monkeypatch):
    db_path = tmp_path / "shared-kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    kb.write_board_metadata("alias-a", name="Alias A")
    kb.write_board_metadata("alias-b", name="Alias B")

    tid = _create_completed_subscription()

    adapter = RecordingAdapter()
    runner = _make_runner(adapter)

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 1
    assert "Kanban" in adapter.sent[0]["text"]
    assert tid in adapter.sent[0]["text"]


def test_kanban_notifier_delivers_global_created_completed_and_blocked_only(tmp_path, monkeypatch):
    db_path = tmp_path / "global-kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    conn = kb.connect()
    try:
        crashed_id = kb.create_task(conn, title="global crash noise", assignee="worker")
        kb.add_global_notify_sub(
            conn,
            platform="telegram",
            chat_id="chat-1",
            notifier_profile="default",
        )
        created_id = kb.create_task(conn, title="global created", assignee="worker")
        done_id = kb.create_task(conn, title="global done", assignee="worker")
        blocked_id = kb.create_task(conn, title="global blocked", assignee="worker")
        kb.complete_task(conn, done_id, summary="done globally")
        kb.block_task(conn, blocked_id, reason="needs Sam")
        kb._append_event(conn, crashed_id, kind="crashed")
    finally:
        conn.close()

    adapter = RecordingAdapter()
    runner = _make_runner(adapter)
    runner._kanban_notifier_profile = "default"

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    texts = [sent["text"] for sent in adapter.sent]
    assert len(texts) == 5
    assert any("default" in text and created_id in text and "created" in text for text in texts)
    assert any("default" in text and done_id in text and "created" in text for text in texts)
    assert any("default" in text and done_id in text and "global done" in text for text in texts)
    assert any("default" in text and blocked_id in text and "created" in text for text in texts)
    assert any("default" in text and blocked_id in text and "global blocked" in text for text in texts)
    assert all(crashed_id not in text for text in texts)


def test_kanban_notifier_dedupes_global_sub_across_alias_boards(tmp_path, monkeypatch):
    db_path = tmp_path / "global-shared-kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    kb.write_board_metadata("alias-a", name="Alias A")
    kb.write_board_metadata("alias-b", name="Alias B")

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="global alias done", assignee="worker")
        kb.add_global_notify_sub(conn, platform="telegram", chat_id="chat-1")
        kb.complete_task(conn, tid, summary="done once")
    finally:
        conn.close()

    adapter = RecordingAdapter()
    runner = _make_runner(adapter)

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 1
    assert tid in adapter.sent[0]["text"]


def test_discord_global_kanban_notifications_use_one_thread_per_task(tmp_path, monkeypatch):
    db_path = tmp_path / "discord-card-threads.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    conn = kb.connect()
    try:
        kb.add_global_notify_sub(conn, platform="discord", chat_id="parent-channel")
        first_id = kb.create_task(conn, title="first threaded card", assignee="worker")
        second_id = kb.create_task(conn, title="second threaded card", assignee="worker")
        kb.complete_task(conn, first_id, summary="first completion")
        kb._append_event(conn, first_id, kind="blocked", payload={"reason": "blocked after done for test"})
        kb.complete_task(conn, second_id, summary="second completion")
    finally:
        conn.close()

    adapter = RecordingDiscordAdapter()
    runner = _make_discord_runner(adapter)

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 5
    sends_by_task = {
        task_id: [sent for sent in adapter.sent if task_id in sent["text"]]
        for task_id in (first_id, second_id)
    }
    assert [sent["metadata"].get("thread_id") for sent in sends_by_task[first_id]] == [
        f"thread-{first_id}",
        f"thread-{first_id}",
        f"thread-{first_id}",
    ]
    assert [sent["metadata"].get("thread_id") for sent in sends_by_task[second_id]] == [
        f"thread-{second_id}",
        f"thread-{second_id}",
    ]
    assert any("created" in sent["text"] for sent in sends_by_task[first_id])
    assert any("created" in sent["text"] for sent in sends_by_task[second_id])
    assert len(adapter.created_threads) == 2
    assert {created["task_id"] for created in adapter.created_threads} == {first_id, second_id}

    conn = kb.connect()
    try:
        assert kb.get_notification_thread(
            conn,
            task_id=first_id,
            platform="discord",
            chat_id="parent-channel",
        ) == f"thread-{first_id}"
        assert kb.get_notification_thread(
            conn,
            task_id=second_id,
            platform="discord",
            chat_id="parent-channel",
        ) == f"thread-{second_id}"
    finally:
        conn.close()


def test_kanban_notifier_claim_prevents_second_watcher_send(tmp_path, monkeypatch):
    db_path = tmp_path / "single-owner.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    tid = _create_completed_subscription()

    adapter1 = RecordingAdapter()
    adapter2 = RecordingAdapter()

    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter1)))
    asyncio.run(_run_one_notifier_tick(monkeypatch, _make_runner(adapter2)))

    assert len(adapter1.sent) == 1
    assert adapter2.sent == []


def test_kanban_notifier_rewinds_claim_if_adapter_disconnects(tmp_path, monkeypatch):
    db_path = tmp_path / "adapter-disconnect.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    tid = _create_completed_subscription()

    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running = True
    runner.adapters = DisconnectedAdapters({Platform.TELEGRAM: RecordingAdapter()})
    runner._kanban_sub_fail_counts = {}

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert [ev.kind for ev in _unseen_terminal_events(tid)] == ["completed"]


def test_kanban_db_path_is_test_isolated_from_real_home():
    hermes_home = Path(kb.kanban_home())
    production_db = Path.home() / ".hermes" / "kanban.db"
    assert kb.kanban_db_path().resolve() != production_db.resolve()

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="x", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
    finally:
        conn.close()

    assert kb.kanban_db_path().resolve().is_relative_to(hermes_home.resolve())
    assert kb.kanban_db_path().resolve() != production_db.resolve()


class FailingAdapter:
    """Adapter whose send() always raises, simulating a transient send error."""

    def __init__(self):
        self.attempts = 0

    async def send(self, chat_id, text, metadata=None):
        self.attempts += 1
        raise RuntimeError("simulated send failure")


def test_kanban_notifier_rewinds_claim_on_send_exception(tmp_path, monkeypatch):
    """A raising adapter rewinds the claim so the next tick can retry.

    This is the second rewind path (distinct from the adapter-disconnect path
    in test_kanban_notifier_rewinds_claim_if_adapter_disconnects). Here the
    adapter is connected and the send call actually fires; the claim must
    still rewind so the event isn't lost when send() raises mid-tick.
    """
    db_path = tmp_path / "send-failure.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()
    tid = _create_completed_subscription()

    adapter = FailingAdapter()
    runner = _make_runner(adapter)

    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    # Send was attempted (so we exercised the failure path, not just the
    # disconnect path) and the claim was rewound — the unseen-events query
    # still returns the event for retry on the next tick.
    assert adapter.attempts >= 1, "send should have been attempted at least once"
    assert [ev.kind for ev in _unseen_terminal_events(tid)] == ["completed"]


def test_notifier_redelivers_same_kind_on_dispatch_cycle(tmp_path, monkeypatch):
    """A retry cycle (crashed → reclaimed → crashed) notifies the user twice.

    Before #21398 the notifier auto-unsubscribed on any terminal event kind
    (gave_up / crashed / timed_out), so the second crash in a respawn cycle
    silently dropped — the subscription was already gone. This test pins the
    new contract: subscription survives non-final terminal events; the
    cursor handles dedup.

    Two crashes ten seconds apart on the same task — both should land on
    the adapter.
    """
    db_path = tmp_path / "redeliver-cycle.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    kb.init_db()

    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="cycle test", assignee="worker")
        kb.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="chat-1")
        # First crash — fired by the dispatcher when the worker PID dies.
        kb._append_event(conn, tid, kind="crashed")
    finally:
        conn.close()

    adapter = RecordingAdapter()
    runner = _make_runner(adapter)
    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    # First crash delivered.
    assert len(adapter.sent) == 1
    assert "crashed" in adapter.sent[0]["text"].lower()

    # Subscription survives — the cursor advanced past event #1, but the
    # row is still there.
    conn = kb.connect()
    try:
        subs = kb.list_notify_subs(conn, tid)
        assert len(subs) == 1, (
            "Subscription must survive a crashed event so a respawn-cycle "
            "second crash also notifies the user (issue #21398)."
        )

        # Second crash — same task, same dispatcher (or a respawn). Append
        # another event to simulate the dispatcher firing crashed a second
        # time during retry.
        kb._append_event(conn, tid, kind="crashed")
    finally:
        conn.close()

    # New tick: the second event has a fresh id past the cursor advance,
    # so it gets claimed and delivered.
    runner = _make_runner(adapter)
    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))

    assert len(adapter.sent) == 2, (
        f"Second crashed event should also notify; got {len(adapter.sent)} "
        f"deliveries (texts: {[d['text'] for d in adapter.sent]})"
    )
    assert "crashed" in adapter.sent[1]["text"].lower()
