"""start_health_poller drives the TrayStateCoordinator (not the tray directly), and speaks "Jarvis
is offline" once per transition into unreachable, not on every failed poll."""
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
        self.calls: list[bool] = []

    def set_unreachable(self, unreachable: bool) -> None:
        self.calls.append(unreachable)


def test_speaks_offline_once_per_transition_and_recovers():
    api = FakeApi([True, False, False, True, True])
    coordinator = FakeCoordinator()
    spoken = []
    stop_event = threading.Event()

    thread = start_health_poller(api, coordinator, spoken.append, interval=0.01,
                                  stop_event=stop_event)
    deadline = time.monotonic() + 2
    while len(coordinator.calls) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_event.set()
    thread.join(timeout=2)

    # healthy_sequence = [True, False, False, True, True] -> unreachable = [False, True, True, False, False]
    assert coordinator.calls[:5] == [False, True, True, False, False]
    assert spoken == ["Jarvis is offline"]
