"""AD1: each daemon logs event=heartbeat mode=<mode> every 300 s; metric filters turn it into Jarvis/Heartbeat."""
import logging, threading
from jarvis import heartbeat


def test_interval_constant():
    assert heartbeat.HEARTBEAT_SECONDS == 300


def test_beats_immediately_then_on_interval(caplog):
    seen = threading.Event()
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)
            if len(records) >= 2:
                seen.set()
    h = Grab()
    logging.getLogger("jarvis.heartbeat").addHandler(h)
    logging.getLogger("jarvis.heartbeat").setLevel(logging.INFO)
    hb = heartbeat.Heartbeat("personal", interval=0.01)
    try:
        hb.start()
        assert seen.wait(2)
    finally:
        hb.stop()
        hb.join(2)
        logging.getLogger("jarvis.heartbeat").removeHandler(h)
    assert not hb.is_alive()
    fields = records[0].jarvis_fields
    assert records[0].jarvis_event == "heartbeat" and fields == {"mode": "personal"}
