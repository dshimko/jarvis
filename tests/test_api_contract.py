"""AD14 /health and AD15 /utterance contracts for a single-mode daemon (additive fields only)."""
import json, subprocess
import pytest
from fastapi.testclient import TestClient
from jarvis import api, brain, outbox
from jarvis.events import EventBus
from jarvis.handler import make_handler
from .conftest import TOKEN, make_item

AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def personal_client(modes, monkeypatch):
    monkeypatch.setattr(brain.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout=json.dumps({"result": "done"}), stderr=""))
    one = {"personal": modes["personal"]}
    app = api.create_app(one, make_handler(one)[0], TOKEN, EventBus(), deployment="aws")
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def test_health_single_mode(personal_client):
    body = personal_client.get("/health", headers=AUTH).json()
    assert body == {"ok": True, "mode": "personal", "deployment": "aws",
                    "modes": {"personal": {"vault": True, "mcp_config": True, "pending": 0}}}


def test_utterance_has_suggested_mode(personal_client, jev_scores):
    r = personal_client.post("/utterance", json={"text": "hello", "mode": "personal"}, headers=AUTH).json()
    assert r == {"reply": "done", "mode_used": "personal", "needs_mode": False, "suggested_mode": None}
    jev_scores["mode"] = ("work", 0.99)
    r = personal_client.post("/utterance", json={"text": "jira status", "mode": "personal"}, headers=AUTH).json()
    assert r["needs_mode"] is True and r["suggested_mode"] == "work"


def test_other_mode_is_404_not_500(personal_client, modes):
    make_item(modes["work"], "w1")
    assert personal_client.get("/outbox?mode=work", headers=AUTH).status_code == 404
    r = personal_client.post("/approve", headers=AUTH, json={"mode": "work", "id": "w1", "body_sha256": "0" * 64,
                                                             "readback_sha256": "0" * 64})
    assert r.status_code == 404


def test_obsidian_uri_uses_configured_vault_name(modes):
    """aws vaults live in /home/jarvis-<mode>/vault; Obsidian knows them as Jarvis-Work / Jarvis-Personal."""
    from dataclasses import replace
    m = replace(modes["personal"], obsidian_vault_name="Jarvis-Personal")
    p = make_item(m, "p1")
    view = api.item_view(m, *outbox.read_item(p), p)
    assert view["obsidian_uri"].startswith("obsidian://open?vault=Jarvis-Personal&file=outbox%2Fp1.md")
