"""Brief 9.3 / AD9: a Syncthing conflict file next to an outbox item blocks it until resolved."""
import pytest
from jarvis import outbox
from .conftest import approve_ok, make_item, meta_of

CONFLICT = "{id}.sync-conflict-20260925-101010-ABCDEFG.md"


def readback_hash(mode, item_id):
    return outbox.readback_sha256(meta_of(outbox.item_path(mode, item_id)))


@pytest.mark.parametrize("name", [CONFLICT, "{id}.md.sync-conflict-20260925-101010-ABCDEFG",
                                  "{id}.sync-conflict-20260925-101010-ABCDEFG"])
def test_conflict_blocks_approve_and_execute(modes, jev_scores, mcp_calls, name):
    m = modes["personal"]
    p = make_item(m, "p1", body="Pickup at 5 works.")
    conflict = m.vault / "outbox" / name.format(id="p1")
    conflict.write_text("other side's edit", encoding="utf-8")
    before = p.read_bytes()
    out = outbox.approve_and_execute(m, "p1", readback_sha256=readback_hash(m, "p1"))
    assert not out.sent and out.reason.startswith("sync conflict")
    assert conflict.name in out.reason
    assert p.read_bytes() == before and meta_of(p)["status"] == "pending"
    assert not mcp_calls
    conflict.unlink()
    out = outbox.approve_and_execute(m, "p1", readback_sha256=readback_hash(m, "p1"))
    assert out.sent and len(mcp_calls) == 1


def test_conflict_is_the_first_gate(modes, jev_scores, mcp_calls, monkeypatch):
    m = modes["work"]
    p = make_item(m, "w1")
    approve_ok(m, "w1")
    (m.vault / "outbox" / CONFLICT.format(id="w1")).write_text("x", encoding="utf-8")
    monkeypatch.setattr(outbox, "_check_identity", lambda *a: pytest.fail("conflict gate must run first"))
    with pytest.raises(outbox.Blocked, match="^sync conflict: resolve w1.sync-conflict"):
        outbox.check_gates(m, *outbox.read_item(p), "w1")
    assert outbox.execute_detailed(m, "w1").reason.startswith("sync conflict")
    assert meta_of(p)["status"] == "approved" and not mcp_calls


def test_other_items_conflicts_do_not_block(modes, jev_scores, mcp_calls):
    """AD9 matches <id>*.sync-conflict-* (fails closed on prefixes); an unrelated id is not affected."""
    m = modes["work"]
    make_item(m, "w1")
    make_item(m, "x9")
    (m.vault / "outbox" / CONFLICT.format(id="x9")).write_text("x", encoding="utf-8")
    assert outbox.approve_and_execute(m, "w1", readback_sha256=readback_hash(m, "w1")).sent


def test_conflict_file_is_never_an_approvable_id(modes):
    with pytest.raises(outbox.NotFound):
        outbox.item_path(modes["work"], "w1.sync-conflict-20260925-101010-ABCDEFG")


def test_conflict_copies_are_not_listed(modes):
    m = modes["work"]
    make_item(m, "w1")
    make_item(m, "w1.sync-conflict-20260925-101010-ABCDEFG")     # a full duplicate item, as Syncthing writes it
    assert [i for i, _ in outbox.pending(m)] == ["w1"]
    assert [meta["id"] for meta, _, _ in outbox.reviewable(m)] == ["w1"]
