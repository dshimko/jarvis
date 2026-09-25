"""10.5: transcripts, replies, and readbacks are never written to disk in either mode. Runs the
flow with file logging configured into tmp_path, feeds distinctive utterance/reply/readback text
through a fake api, then asserts no file under tmp_path contains any of it."""
from jarvis_client import logging_setup
from jarvis_client.flow import VoiceFlow

from fakes import FakeApi, FakeClock, Recorder

SECRET_UTTERANCE_WORK = "XYZZY-WORK-UTTERANCE-3f9a"
SECRET_REPLY_WORK = "XYZZY-WORK-REPLY-1c2b"
SECRET_UTTERANCE_PERSONAL = "XYZZY-PERSONAL-UTTERANCE-77aa"
SECRET_REPLY_PERSONAL = "XYZZY-PERSONAL-REPLY-99bb"
SECRET_READBACK = "XYZZY-READBACK-Please wire funds to account 555"


def _run_flow_with_logging(tmp_path):
    log_path = tmp_path / "client.log"
    logging_setup.setup_logging(str(log_path))

    api = FakeApi(utterance_responses=[
        {"reply": SECRET_REPLY_WORK, "mode_used": "work", "needs_mode": False},
        {"reply": SECRET_REPLY_PERSONAL, "mode_used": "personal", "needs_mode": False},
    ], outbox_items=[{
        "id": "money-1", "readback": SECRET_READBACK,
        "readback_sha256": __import__("hashlib").sha256(SECRET_READBACK.encode()).hexdigest(),
        "body_sha256": "body-hash", "tone_flagged": False,
    }])
    flow = VoiceFlow(api, Recorder(), FakeClock())

    flow.on_utterance(SECRET_UTTERANCE_WORK, "work")
    flow.on_utterance(SECRET_UTTERANCE_PERSONAL, "personal")
    flow.on_utterance("approve money", "work")
    flow.on_utterance("confirm", "work")
    return log_path


def test_no_secret_text_reaches_any_file_under_tmp_path(tmp_path):
    _run_flow_with_logging(tmp_path)

    secrets = [SECRET_UTTERANCE_WORK, SECRET_REPLY_WORK, SECRET_UTTERANCE_PERSONAL,
               SECRET_REPLY_PERSONAL, SECRET_READBACK]
    for path in tmp_path.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for secret in secrets:
            assert secret not in content, f"{secret!r} leaked into {path}"
