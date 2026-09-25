"""events: thread-safe bus, outbox poller (B6), outbox listener, SSE framing, schedule_done."""
import asyncio, json, threading
import pytest
from jarvis import api, events, main, outbox
from jarvis.modes import make_redactor
from .conftest import approve_ok, make_item


def run_bus(fn):
    """Run fn(bus, queue) inside a live loop and return what the queue got."""
    async def inner():
        bus = events.EventBus(maxsize=3)
        bus.attach_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        await asyncio.to_thread(fn, bus)
        await asyncio.sleep(0.01)
        got = []
        while not q.empty():
            got.append(q.get_nowait())
        return got
    return asyncio.run(inner())


def test_publish_from_thread():
    got = run_bus(lambda bus: bus.publish("item_sent", {"mode": "work", "id": "a"}))
    assert got == [{"type": "item_sent", "data": {"mode": "work", "id": "a"}}]


def test_bounded_queue_drops_oldest():
    def many(bus):
        for i in range(5):
            bus.publish("item_sent", {"mode": "work", "id": str(i)})
    assert [e["data"]["id"] for e in run_bus(many)] == ["2", "3", "4"]


def test_unknown_type_rejected_and_no_loop_is_noop():
    bus = events.EventBus()
    with pytest.raises(ValueError):
        bus.publish("nope", {})
    bus.publish("item_sent", {"mode": "work"})     # no loop attached: silently dropped


def test_poller_seeds_silently_then_emits_new(modes):
    make_item(modes["work"], "old")
    published = []

    class Bus:
        def publish(self, kind, data):
            published.append((kind, data))
    poller = events.OutboxPoller(modes, Bus())
    poller.seed()
    assert poller.poll_once() == [] and not published
    make_item(modes["personal"], "new1", body="\nFirst line\nsecond")
    assert poller.poll_once() == [("personal", "new1")]
    assert published == [("pending_new", {"mode": "personal", "id": "new1", "first_line": "First line",
                                           "obsidian_uri": "obsidian://open?vault=Jarvis-Personal&file=outbox%2Fnew1.md"})]
    assert poller.poll_once() == []


def test_poller_uri_uses_real_file_name(modes):
    published = []

    class Bus:
        def publish(self, kind, data):
            published.append(data)
    poller = events.OutboxPoller(modes, Bus())
    poller.seed()
    p = make_item(modes["work"], "the-id")
    p.rename(p.with_name("Renamed File.md"))
    poller.poll_once()
    assert published[0]["id"] == "the-id"
    assert published[0]["obsidian_uri"].endswith("file=outbox%2FRenamed%20File.md")


def test_sse_stream_ends_on_shutdown(modes):
    async def inner():
        bus = events.EventBus()
        bus.attach_loop(asyncio.get_running_loop())
        exiting = threading.Event()

        async def connected():
            return False
        gen = api.sse_stream(bus, make_redactor(modes), connected, exiting, heartbeat=0.01, poll=0.01)
        assert await gen.__anext__() == ": heartbeat\n\n"
        exiting.set()
        chunks = [c async for c in gen]
        return chunks
    assert asyncio.run(asyncio.wait_for(inner(), timeout=5)) in ([], [": heartbeat\n\n"])


def test_poller_thread_stops(modes):
    poller = events.OutboxPoller(modes, events.EventBus(), interval=0.01)
    poller.start()
    poller.stop()
    poller.join(timeout=2)
    assert not poller.is_alive()


def test_execute_publishes_sent_and_blocked(modes, jev_scores, mcp_calls):
    seen = []
    listener = lambda *a: seen.append(a)
    outbox.add_listener(listener)
    try:
        make_item(modes["work"], "a1")
        approve_ok(modes["work"], "a1")
        outbox.execute(modes["work"], "a1")
        make_item(modes["work"], "a2", tool="nope")
        approve_ok(modes["work"], "a2")
        outbox.execute(modes["work"], "a2")
    finally:
        outbox.remove_listener(listener)
    assert seen[0] == ("item_sent", "work", "a1", None)
    assert seen[1][:3] == ("item_blocked", "work", "a2") and "not an allowed write tool" in seen[1][3]


def test_outbox_listener_adapter():
    got = run_bus(lambda bus: events.outbox_listener(bus)("item_blocked", "work", "x", "expired"))
    assert got[0]["data"] == {"mode": "work", "id": "x", "reason": "expired"}


def test_sse_format_redacts(modes):
    text = api.sse_format({"type": "item_blocked", "data": {"reason": "Bearer xoxp-work-secret-1111"}},
                          make_redactor(modes))
    assert text.startswith("event: item_blocked\ndata: ") and text.endswith("\n\n")
    assert json.loads(text.split("data: ", 1)[1]) == {"reason": "Bearer [redacted]"}


def test_sse_stream_heartbeat_then_event(modes):
    async def inner():
        bus = events.EventBus()
        bus.attach_loop(asyncio.get_running_loop())
        state = {"n": 0}

        async def disconnected():
            state["n"] += 1
            return state["n"] > 20
        gen = api.sse_stream(bus, make_redactor(modes), disconnected, threading.Event(), heartbeat=0.01, poll=0.01)
        first = await gen.__anext__()
        bus.publish("schedule_done", {"mode": "work", "job": "morning-brief"})
        second = await gen.__anext__()
        await gen.aclose()
        return first, second
    first, second = asyncio.run(inner())
    assert first == ": heartbeat\n\n"
    assert second.startswith("event: schedule_done\n")


def test_run_job_publishes_schedule_done(modes, monkeypatch):
    published = []

    class Bus:
        def publish(self, kind, data):
            published.append((kind, data))
    monkeypatch.setattr(main.brain, "ask_detailed", lambda mode, prompt: main.brain.Answer("ok", True))
    main.run_job(modes["work"], "morning-brief", Bus())
    monkeypatch.setattr(main.brain, "ask_detailed", lambda mode, prompt: main.brain.Answer("Claude Code failed", False))
    main.run_job(modes["work"], "end-of-day", Bus())
    monkeypatch.setattr(main.brain, "ask_detailed", lambda mode, prompt: 1 / 0)
    main.run_job(modes["personal"], "ofw-check", Bus())
    assert published == [("schedule_done", {"mode": "work", "job": "morning-brief", "ok": True}),
                         ("schedule_done", {"mode": "work", "job": "end-of-day", "ok": False}),
                         ("schedule_done", {"mode": "personal", "job": "ofw-check", "ok": False})]


def test_schedules_registered(modes):
    s = main.schedules(modes, events.EventBus())
    s.start(paused=True)
    try:
        jobs = s.get_jobs()
        assert {j.args[1] for j in jobs} == {"morning-brief", "end-of-day", "evening-review", "ofw-check"}
        assert all(j.misfire_grace_time == 3600 and j.coalesce for j in jobs)
    finally:
        s.shutdown(wait=False)


def test_main_args():
    a = main.parse_args(["--headless", "--no-channels"])
    assert a.headless and a.no_channels
    assert not hasattr(main, "voice")
