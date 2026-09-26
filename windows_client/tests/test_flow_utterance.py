"""AD15 needs_mode / suggested_mode round trip: the client speaks a fixed prompt naming the
suggested mode, and only the spoken word "confirm" within the timeout re-sends the ORIGINAL text
to the OTHER endpoint (mode=suggested_mode, mode_confirmed=True) via the Api facade -- anything
else (a different word, silence, a timeout) cancels with no re-send."""
from jarvis_client.flow import VoiceFlow

from fakes import FakeApi, FakeClock, Recorder


def test_plain_utterance_speaks_reply_and_needs_no_follow_up():
    api = FakeApi(utterance_responses=[{"reply": "Noted.", "mode_used": "work",
                                         "needs_mode": False, "suggested_mode": None}])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("remind me to call bob", "work")

    assert api.utterance_calls == [("remind me to call bob", "work", False)]
    assert speak.lines == ["Noted."]


def test_needs_mode_speaks_suggested_mode_prompt():
    api = FakeApi(utterance_responses=[
        {"reply": "ignored", "mode_used": None, "needs_mode": True, "suggested_mode": "personal"},
    ])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("draft an email to the coparent", "work")

    assert speak.lines == ["That sounds like personal. Say confirm to send it there."]


def test_confirm_within_timeout_resends_original_text_to_suggested_mode():
    api = FakeApi(utterance_responses=[
        {"reply": "ignored", "mode_used": None, "needs_mode": True, "suggested_mode": "personal"},
        {"reply": "Done in personal mode.", "mode_used": "personal", "needs_mode": False,
         "suggested_mode": None},
    ])
    speak = Recorder()
    clock = FakeClock()
    flow = VoiceFlow(api, speak, clock)

    flow.on_utterance("draft an email to the coparent", "work")
    clock.advance(5)
    flow.on_utterance("confirm", "work")

    assert api.utterance_calls[0] == ("draft an email to the coparent", "work", False)
    assert api.utterance_calls[1] == ("draft an email to the coparent", "personal", True)
    assert speak.lines[-1] == "Done in personal mode."


def test_confirm_after_the_timeout_cancels_without_resending():
    api = FakeApi(utterance_responses=[
        {"reply": "ignored", "mode_used": None, "needs_mode": True, "suggested_mode": "personal"},
    ])
    speak = Recorder()
    clock = FakeClock()
    flow = VoiceFlow(api, speak, clock)

    flow.on_utterance("draft an email to the coparent", "work")
    clock.advance(61)
    flow.on_utterance("confirm", "work")

    assert len(api.utterance_calls) == 1  # no re-send
    assert speak.lines[-1] == "Nothing was sent."


def test_any_other_word_cancels_immediately():
    api = FakeApi(utterance_responses=[
        {"reply": "ignored", "mode_used": None, "needs_mode": True, "suggested_mode": "personal"},
        {"reply": "Handled as new.", "mode_used": "work", "needs_mode": False, "suggested_mode": None},
    ])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("draft an email to the coparent", "work")
    flow.on_utterance("no thanks", "work")

    assert len(api.utterance_calls) == 1  # "no thanks" is not treated as a new utterance either
    assert speak.lines[-1] == "Nothing was sent."


def test_confirm_from_the_other_hotkey_cancels():
    """M8: the confirm must come from the SAME hotkey mode as the original ambiguous utterance --
    pressing the other hotkey and saying "confirm" does not re-send (mirrors _handle_confirm's
    own mode check)."""
    api = FakeApi(utterance_responses=[
        {"reply": "ignored", "mode_used": None, "needs_mode": True, "suggested_mode": "personal"},
    ])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("draft an email to the coparent", "work")
    flow.on_utterance("confirm", "personal")

    assert len(api.utterance_calls) == 1  # no re-send
    assert speak.lines[-1] == "Nothing was sent."


def test_confirm_recorded_before_the_prompt_finished_is_stale_and_cancels():
    """M8: mirrors _handle_confirm's stale-audio guard -- a recording that started before the
    "say confirm" prompt finished being spoken cannot possibly be a real response to it."""
    api = FakeApi(utterance_responses=[
        {"reply": "ignored", "mode_used": None, "needs_mode": True, "suggested_mode": "personal"},
    ])
    speak = Recorder()
    clock = FakeClock()
    flow = VoiceFlow(api, speak, clock)

    flow.on_utterance("draft an email to the coparent", "work", recording_started_at=0.0)
    clock.advance(5)
    # This "confirm" recording started (at t=1, before the prompt at t=0 finished being spoken --
    # use a negative offset relative to prompt_finished_at to simulate overlapping stale audio).
    flow.on_utterance("confirm", "work", recording_started_at=-1.0)

    assert len(api.utterance_calls) == 1  # no re-send
    assert speak.lines[-1] == "Nothing was sent."


def test_needs_mode_without_a_suggested_mode_just_speaks_the_reply():
    """Defensive: the contract says suggested_mode is null only when needs_mode is false, but the
    client must not crash or hang waiting for a confirm it can never resolve if that ever happens."""
    api = FakeApi(utterance_responses=[
        {"reply": "Which mode?", "mode_used": None, "needs_mode": True, "suggested_mode": None},
    ])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("something ambiguous", "work")

    assert speak.lines == ["Which mode?"]
    # and the next utterance is handled as new, not swallowed by a stuck awaiting-confirm state
    flow.on_utterance("confirm", "work")
    assert len(api.utterance_calls) == 2
