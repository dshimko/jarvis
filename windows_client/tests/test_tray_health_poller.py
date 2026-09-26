"""start_health_poller drives the TrayStateCoordinator (not the tray directly) for two modes: the
icon goes grey only when EVERY mode is unreachable, "Jarvis is offline" is spoken once per
transition into that all-down state (not on every failed poll, and not when only one mode is
down), and each mode's status is reported individually via set_mode_health for the tooltip."""
import threading
import time

from jarvis_client.tray import start_health_poller


class FakeApi:
    """Replays `healthy_sequence`, then repeats its last value forever (so the poller settles
    into a steady state instead of raising once the test stops feeding it new values)."""

    def __init__(self, healthy_sequence):
        self._sequence = list(healthy_sequence)
        self._index = 0

    def health(self):
        idx = min(self._index, len(self._sequence) - 1)
        self._index += 1
        if not self._sequence[idx]:
            raise ConnectionError("refused")
        return {"ok": True}


class FakeCoordinator:
    def __init__(self):
        self.unreachable_calls: list[bool] = []
        self.mode_health_calls: list[tuple[str, bool]] = []

    def set_unreachable(self, unreachable: bool) -> None:
        self.unreachable_calls.append(unreachable)

    def set_mode_health(self, mode: str, healthy: bool) -> None:
        self.mode_health_calls.append((mode, healthy))


def test_icon_goes_grey_only_when_both_modes_are_down():
    # work stays up throughout; personal goes down then recovers. The icon must never go grey.
    apis = {"work": FakeApi([True] * 10), "personal": FakeApi([True, False, False, True, True])}
    coordinator = FakeCoordinator()
    spoken = []
    stop_event = threading.Event()

    start_health_poller(apis, coordinator, spoken.append, interval=0.01, stop_event=stop_event)
    deadline = time.monotonic() + 2
    while len(coordinator.unreachable_calls) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_event.set()

    assert all(call is False for call in coordinator.unreachable_calls[:5])
    assert spoken == []
    assert ("personal", False) in coordinator.mode_health_calls
    assert ("work", True) in coordinator.mode_health_calls


def test_speaks_offline_once_when_every_mode_goes_down_and_recovers():
    apis = {"work": FakeApi([True, False, False, True, True]),
            "personal": FakeApi([True, False, False, True, True])}
    coordinator = FakeCoordinator()
    spoken = []
    stop_event = threading.Event()

    thread = start_health_poller(apis, coordinator, spoken.append, interval=0.01,
                                  stop_event=stop_event)
    deadline = time.monotonic() + 2
    while len(coordinator.unreachable_calls) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_event.set()
    thread.join(timeout=2)

    # both modes: [True, False, False, True, True] -> all_down = [False, True, True, False, False]
    assert coordinator.unreachable_calls[:5] == [False, True, True, False, False]
    assert spoken == ["Jarvis is offline"]
