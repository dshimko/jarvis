"""TrayStateCoordinator: the single source of truth for the tray icon's resting state, so one
signal (a finished "thinking" action) never clobbers another (daemon-unreachable, or the other
hotkey still recording)."""
from jarvis_client.tray import TrayStateCoordinator


class FakeTrayApp:
    def __init__(self):
        self.states: list[str] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)


def test_rest_defaults_to_idle():
    tray_app = FakeTrayApp()
    coordinator = TrayStateCoordinator(tray_app)

    coordinator.rest()

    assert tray_app.states[-1] == "idle"


def test_unreachable_wins_over_a_finished_recording():
    tray_app = FakeTrayApp()
    coordinator = TrayStateCoordinator(tray_app)

    coordinator.set_unreachable(True)
    coordinator.set_recording("work", True)
    coordinator.set_recording("work", False)  # "thinking" finished, would naively go to idle

    assert tray_app.states[-1] == "daemon-unreachable"


def test_the_other_modes_recording_wins_over_idle():
    tray_app = FakeTrayApp()
    coordinator = TrayStateCoordinator(tray_app)

    coordinator.set_recording("personal", True)   # personal is mid-recording
    coordinator.set_recording("work", True)       # work starts too
    coordinator.set_transient("thinking")         # work finished recording, now "thinking"
    coordinator.set_recording("work", False)      # work's _process finishes -> rest()

    assert tray_app.states[-1] == "recording-personal"


def test_recording_wins_over_idle_but_not_over_unreachable():
    tray_app = FakeTrayApp()
    coordinator = TrayStateCoordinator(tray_app)

    coordinator.set_recording("work", True)
    assert tray_app.states[-1] == "recording-work"

    coordinator.set_unreachable(True)
    assert tray_app.states[-1] == "daemon-unreachable"

    coordinator.set_unreachable(False)
    assert tray_app.states[-1] == "recording-work"


def test_transient_state_is_shown_immediately_and_rest_restores_afterwards():
    tray_app = FakeTrayApp()
    coordinator = TrayStateCoordinator(tray_app)

    coordinator.set_transient("thinking")
    assert tray_app.states[-1] == "thinking"

    coordinator.rest()
    assert tray_app.states[-1] == "idle"


def test_thinking_survives_a_healthy_poll_update_mid_utterance():
    """A long /utterance call (up to 960s) means several /health polls land while "thinking" is
    showing; a healthy poll must not revert the icon to idle before rest() is actually called."""
    tray_app = FakeTrayApp()
    coordinator = TrayStateCoordinator(tray_app)

    coordinator.set_transient("thinking")
    coordinator.set_unreachable(False)  # a healthy poll firing mid-utterance

    assert tray_app.states[-1] == "thinking"


def test_set_unreachable_is_a_noop_on_no_change():
    """No redundant _apply()/set_state() call when the value doesn't actually change (also what
    makes the previous test work: a healthy poll after another healthy poll is a no-op)."""
    tray_app = FakeTrayApp()
    coordinator = TrayStateCoordinator(tray_app)
    coordinator.rest()  # -> idle
    before = len(tray_app.states)

    coordinator.set_unreachable(False)  # already reachable, no change

    assert len(tray_app.states) == before
