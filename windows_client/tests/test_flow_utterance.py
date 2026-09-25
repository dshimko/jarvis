"""needs_mode round trip: the confirming utterance must come from the matching hotkey, and an
answer naming the other mode gets a hint rather than a mode switch."""
from jarvis_client.flow import VoiceFlow

from fakes import FakeApi, FakeClock, Recorder


def test_plain_utterance_speaks_reply_and_needs_no_follow_up():
    api = FakeApi(utterance_responses=[{"reply": "Noted.", "mode_used": "work",
                                         "needs_mode": False}])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("remind me to call bob", "work")

    assert api.utterance_calls == [("remind me to call bob", "work", False)]
    assert speak.lines == ["Noted."]


def test_needs_mode_then_matching_hotkey_confirms_original_text():
    api = FakeApi(utterance_responses=[
        {"reply": "Which mode, work or personal?", "mode_used": None, "needs_mode": True},
        {"reply": "Done in work mode.", "mode_used": "work", "needs_mode": False},
    ])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("draft an email to the team", "work")
    flow.on_utterance("work", "work")

    assert api.utterance_calls[0] == ("draft an email to the team", "work", False)
    assert api.utterance_calls[1] == ("draft an email to the team", "work", True)
    assert speak.lines == ["Which mode, work or personal?", "Done in work mode."]


def test_needs_mode_confirm_word_also_confirms():
    api = FakeApi(utterance_responses=[
        {"reply": "Which mode?", "mode_used": None, "needs_mode": True},
        {"reply": "Done.", "mode_used": "personal", "needs_mode": False},
    ])
    flow = VoiceFlow(api, Recorder(), FakeClock())

    flow.on_utterance("something ambiguous", "personal")
    flow.on_utterance("confirm", "personal")

    assert api.utterance_calls[1] == ("something ambiguous", "personal", True)


def test_needs_mode_answer_naming_other_mode_gives_hotkey_hint_and_keeps_waiting():
    api = FakeApi(utterance_responses=[
        {"reply": "Which mode?", "mode_used": None, "needs_mode": True},
        {"reply": "Done in personal mode.", "mode_used": "personal", "needs_mode": False},
    ])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("something ambiguous", "work")
    flow.on_utterance("personal", "work")  # pressed W's hotkey but said the other mode's name

    assert speak.lines[-1] == "Press Ctrl+Alt+P for personal"
    assert len(api.utterance_calls) == 1  # no re-post yet, still waiting

    # now they press the matching hotkey and it goes through
    flow.on_utterance("personal", "personal")
    assert api.utterance_calls[1] == ("something ambiguous", "personal", True)
    assert speak.lines[-1] == "Done in personal mode."


def test_unrelated_answer_after_needs_mode_is_treated_as_a_new_utterance():
    api = FakeApi(utterance_responses=[
        {"reply": "Which mode?", "mode_used": None, "needs_mode": True},
        {"reply": "Handled as new.", "mode_used": "work", "needs_mode": False},
    ])
    speak = Recorder()
    flow = VoiceFlow(api, speak, FakeClock())

    flow.on_utterance("something ambiguous", "work")
    flow.on_utterance("what's the weather", "work")

    # the second call is a fresh /utterance for the new text, not a re-post of the original
    assert api.utterance_calls[1] == ("what's the weather", "work", False)
    assert speak.lines[-1] == "Handled as new."
