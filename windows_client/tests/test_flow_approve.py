"""Approve intent + read-back + confirm flow: id matching, the hash check, and the 60s confirm
window (mode change / timeout / tone-flagged reconfirm all cancel unless done exactly right)."""
import hashlib

from jarvis_client.flow import VoiceFlow

from fakes import FakeApi, FakeClock, Recorder


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _item(item_id: str, readback: str, body_sha256: str = "body-hash", tone_flagged=False,
          bad_hash=False) -> dict:
    return {
        "id": item_id,
        "readback": readback,
        "readback_sha256": "0" * 64 if bad_hash else _sha(readback),
        "body_sha256": body_sha256,
        "tone_flagged": tone_flagged,
    }


# -- id matching ------------------------------------------------------------------------------

def test_unique_slug_match_starts_the_readback():
    items = [_item("2026-09-24-email-bob", "Send an email to Bob."),
              _item("2026-09-24-email-alice", "Send an email to Alice.")]
    api = FakeApi(outbox_items=items)
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve bob", "work")

    assert speak.lines[0] == "Send an email to Bob."
    assert speak.lines[1] == "Say confirm to send."


def test_ambiguous_slug_match_asks_which_one():
    items = [_item("2026-09-24-email-bob", "Send an email to Bob."),
              _item("2026-09-24-email-alice", "Send an email to Alice.")]
    api = FakeApi(outbox_items=items)
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve email", "work")  # substring of both ids

    assert speak.lines == ["Which one? You have 2: 2026-09-24-email-bob; 2026-09-24-email-alice"]
    assert api.approve_calls == []


def test_bare_approve_works_only_with_exactly_one_item():
    api = FakeApi(outbox_items=[_item("only-one", "Send the only draft.")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve", "work")

    assert speak.lines[0] == "Send the only draft."


def test_bare_approve_with_multiple_items_is_ambiguous():
    items = [_item("a", "A"), _item("b", "B")]
    api = FakeApi(outbox_items=items)
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve", "work")

    assert speak.lines == ["Which one? You have 2: a; b"]


def test_short_spoken_token_never_matches():
    api = FakeApi(outbox_items=[_item("ab-item", "Body")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve ab", "work")  # normalized token "ab" has length 2

    assert speak.lines == ["Which one? You have 1: ab-item"]


# -- hash mismatch ------------------------------------------------------------------------------

def test_hash_mismatch_refuses_to_speak_the_readback():
    api = FakeApi(outbox_items=[_item("changed-item", "New body.", bad_hash=True)])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve changed", "work")

    assert speak.lines == ["That item changed, try again"]
    assert api.approve_calls == []


# -- confirm window -------------------------------------------------------------------------

def test_no_post_until_the_confirm_phrase_is_heard():
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")])
    flow = VoiceFlow(api, Recorder(), FakeClock())

    flow.on_utterance("approve draft", "work")
    assert api.approve_calls == []  # readback + prompt spoken, nothing posted yet

    flow.on_utterance("not the right phrase", "work")
    assert api.approve_calls == []


def test_confirm_posts_readback_hash_equal_to_sha256_of_the_spoken_readback():
    readback = "Email Bob to reschedule at 5pm."
    api = FakeApi(outbox_items=[_item("draft-1", readback, body_sha256="abc123")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve draft", "work")
    flow.on_utterance("confirm", "work")

    assert len(api.approve_calls) == 1
    mode, item_id, body_sha256, readback_sha256, reconfirm = api.approve_calls[0]
    assert mode == "work" and item_id == "draft-1" and body_sha256 == "abc123"
    assert readback_sha256 == _sha(readback) == _sha(speak.lines[0])
    assert reconfirm is False


def test_tone_flagged_item_needs_confirm_reconfirm():
    api = FakeApi(outbox_items=[_item("hot-1", "Tell them it's overdue.", tone_flagged=True)])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve hot", "work")
    assert speak.lines[1] == "This one was tone flagged. Say confirm reconfirm to send."

    flow.on_utterance("confirm reconfirm", "work")

    assert len(api.approve_calls) == 1
    assert api.approve_calls[0][4] is True  # reconfirm


def test_plain_confirm_cancels_a_tone_flagged_item():
    api = FakeApi(outbox_items=[_item("hot-1", "Tell them it's overdue.", tone_flagged=True)])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve hot", "work")
    flow.on_utterance("confirm", "work")

    assert speak.lines[-1] == "Nothing was sent."
    assert api.approve_calls == []


def test_timeout_cancels():
    clock = FakeClock()
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, clock)

    flow.on_utterance("approve draft", "work")
    clock.advance(61)
    flow.on_utterance("confirm", "work")

    assert speak.lines[-1] == "Nothing was sent."
    assert api.approve_calls == []


def test_mode_change_cancels():
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve draft", "work")
    flow.on_utterance("confirm", "personal")

    assert speak.lines[-1] == "Nothing was sent."
    assert api.approve_calls == []


def test_anything_else_cancels():
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve draft", "work")
    flow.on_utterance("what time is it", "work")

    assert speak.lines[-1] == "Nothing was sent."
    assert api.approve_calls == []


def test_approve_speaks_the_result_on_success():
    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")],
                   approve_result={"result": "Sent to #general.", "sent": True, "blocked": None})
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve draft", "work")
    flow.on_utterance("confirm", "work")

    assert speak.lines[-1] == "Sent to #general."


def test_approve_error_speaks_a_friendly_message():
    class Boom(Exception):
        def __init__(self):
            self.status_code = 409

    api = FakeApi(outbox_items=[_item("draft-1", "Send it.")], approve_error=Boom())
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("approve draft", "work")
    flow.on_utterance("confirm", "work")

    assert speak.lines[-1] == "That item changed, try again."
