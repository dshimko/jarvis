"""M2: on_utterance is serialized with a lock, and a confirm is only honored from a recording
that started after the confirm prompt finished being spoken."""
import hashlib
import threading

from jarvis_client.flow import VoiceFlow

from fakes import FakeApi, FakeClock, Recorder


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _item(item_id: str, readback: str) -> dict:
    return {"id": item_id, "readback": readback, "readback_sha256": _sha(readback),
            "body_sha256": "body-hash", "tone_flagged": False}


def test_confirm_from_a_recording_started_before_the_prompt_finished_is_rejected():
    clock = FakeClock(start=100.0)
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, clock)

    flow.on_utterance("approve draft", "work")  # prompt_finished_at == 100.0

    # A recording that had already started (say, at t=95) before the prompt even finished --
    # e.g. stray audio buffered from an earlier, unrelated press -- must not be honored even
    # though its transcript happens to say "confirm".
    flow.on_utterance("confirm", "work", recording_started_at=95.0)

    assert speak.lines[-1] == "Nothing was sent."
    assert api.approve_calls == []


def test_confirm_from_a_recording_started_after_the_prompt_is_accepted():
    clock = FakeClock(start=100.0)
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, clock)

    flow.on_utterance("approve draft", "work")
    flow.on_utterance("confirm", "work", recording_started_at=101.0)

    assert len(api.approve_calls) == 1


def test_concurrent_confirms_produce_a_single_post():
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve draft", "work")

    barrier = threading.Barrier(5)

    def confirm():
        barrier.wait(timeout=5)
        flow.on_utterance("confirm", "work")

    threads = [threading.Thread(target=confirm) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(api.approve_calls) == 1
