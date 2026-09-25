"""outbox.approve / execute: D1, D2, D4, D5, D12, D13, D16, D17."""
import datetime as dt, json, os
import pytest
from jarvis import jev, ledger, outbox
from .conftest import FakeResult, approve_ok, make_item, meta_of, now_iso


# ---- D1: only pending / approved are approvable ----

@pytest.mark.parametrize("status", ["sent", "expired", "sending", "failed", "draft"])
def test_approve_refuses_non_approvable_status(modes, status):
    p = make_item(modes["work"], "s1", status=status)
    before = p.read_bytes()
    with pytest.raises(outbox.Conflict):
        approve_ok(modes["work"], "s1")
    assert p.read_bytes() == before


@pytest.mark.parametrize("status", ["pending", "approved"])
def test_approve_accepts_pending_and_reapprove(modes, status):
    p = make_item(modes["work"], "s2", status=status)
    approve_ok(modes["work"], "s2")
    assert meta_of(p)["status"] == "approved"


def test_sent_item_cannot_be_resent(modes, jev_scores, mcp_calls):
    m = modes["work"]
    make_item(m, "once")
    approve_ok(m, "once")
    assert outbox.execute(m, "once") == "Sent once."
    with pytest.raises(outbox.Conflict):
        approve_ok(m, "once")
    assert len(mcp_calls) == 1


# ---- D2: mode and id are checked before writing ----

def test_approve_refuses_other_mode_item(modes):
    p = make_item(modes["personal"], "pers1")
    target = modes["work"].vault / "outbox" / "pers1.md"
    target.write_bytes(p.read_bytes())
    with pytest.raises(outbox.Conflict, match="mode mismatch"):
        approve_ok(modes["work"], "pers1")
    assert meta_of(target)["status"] == "pending"


def test_approve_refuses_id_mismatch(modes):
    p = make_item(modes["work"], "real-id")
    os.rename(p, p.with_name("other-id.md"))
    with pytest.raises(outbox.Conflict, match="id mismatch"):
        approve_ok(modes["work"], "other-id")


# ---- D4: id pattern and containment ----

@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 129, "a.b", "..", "a b", "%2e%2e"])
def test_bad_ids_rejected(modes, bad):
    with pytest.raises(outbox.NotFound):
        outbox.item_path(modes["work"], bad)


def test_symlinked_item_outside_outbox_rejected(modes, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\nid: link\n---\nx\n", encoding="utf-8")
    (modes["work"].vault / "outbox" / "link.md").symlink_to(outside)
    with pytest.raises(outbox.NotFound):
        outbox.item_path(modes["work"], "link")


def test_unknown_id_not_found(modes):
    with pytest.raises(outbox.NotFound):
        approve_ok(modes["work"], "nope")


# ---- D5: strict reconfirm only after a tone flag ----

def test_strict_reconfirm_requires_tone_flag(modes):
    make_item(modes["personal"], "t1")
    with pytest.raises(outbox.BadReconfirm):
        approve_ok(modes["personal"], "t1", reconfirm=True, strict_reconfirm=True)


def test_strict_reconfirm_after_tone_flag(modes):
    p = make_item(modes["personal"], "t2", status="approved", last_block="tone flag: reread")
    meta, body = outbox.read_item(p)
    ledger.flag_tone("personal", "t2", outbox.approval_hash(meta, body))
    approve_ok(modes["personal"], "t2", reconfirm=True, strict_reconfirm=True)
    assert ledger.is_reconfirmed("personal", "t2", outbox.approval_hash(meta, body))
    assert "reconfirmed" not in meta_of(p)


def test_text_channel_reconfirm_unchanged(modes):
    p = make_item(modes["personal"], "t3")
    approve_ok(modes["personal"], "t3", reconfirm=True)
    meta, body = outbox.read_item(p)
    assert ledger.is_reconfirmed("personal", "t3", outbox.approval_hash(meta, body))


# ---- D12: deterministic readback ----

def test_readback_is_deterministic_and_complete():
    meta = {"mode": "work", "server": "slack", "tool": "post_message",
            "args": json.dumps({"text": "See you at 5", "channel": "C1", "blocks": [{"b": 2, "a": 1}], "n": 3})}
    rb = outbox.render_readback(meta)
    assert rb == ('mode: work\nserver: slack\ntool: post_message\nblocks: [{"a":1,"b":2}]\n'
                  'channel: "C1"\nn: 3\ntext: "See you at 5"')
    as_dict = {**meta, "args": json.loads(meta["args"])}
    assert outbox.render_readback(as_dict) == rb


def test_readback_changes_with_args_not_body(modes):
    m = modes["work"]
    a = meta_of(make_item(m, "r1", body="See you at 5"))
    b = meta_of(make_item(m, "r1", body="See you at 5", args={"channel": "C123", "text": "See you at 6"}))
    assert outbox.readback_sha256(a) != outbox.readback_sha256(b)


def test_hash_mismatch_rejected_before_write(modes):
    m = modes["work"]
    p = make_item(m, "hm")
    meta, body = outbox.read_item(p)
    with pytest.raises(outbox.Conflict):
        approve_ok(m, "hm", body_sha256="0" * 64, readback_sha256=outbox.readback_sha256(meta))
    with pytest.raises(outbox.Conflict):
        approve_ok(m, "hm", body_sha256=outbox.body_sha256(body), readback_sha256="0" * 64)
    assert meta_of(p)["status"] == "pending"


# ---- D13: agent-preset gate fields ----

def test_preset_gate_fields_are_rewritten(modes):
    p = make_item(modes["personal"], "g1", reconfirmed=True, approved_at="2000-01-01T00:00:00",
                  approved_sha256="f" * 64)
    approve_ok(modes["personal"], "g1")
    meta = meta_of(p)
    assert "reconfirmed" not in meta
    assert meta["approved_at"] != "2000-01-01T00:00:00"
    assert meta["approved_sha256"] == outbox.approval_hash(meta, outbox.read_item(p)[1])


def test_future_created_blocked(modes, jev_scores, mcp_calls):
    m = modes["work"]
    make_item(m, "fut", created=now_iso(dt.timedelta(hours=2)))
    approve_ok(m, "fut")
    assert outbox.execute(m, "fut") == "Blocked fut: created in the future"
    assert not mcp_calls


def test_obsidian_approved_without_hash_blocked(modes, jev_scores, mcp_calls):
    """D17: status: approved set by hand has no approved_sha256, so execute refuses."""
    m = modes["work"]
    make_item(m, "manual", status="approved", approved_at=now_iso())
    assert outbox.execute(m, "manual") == "Blocked manual: not approved"
    assert not mcp_calls
    assert not hasattr(outbox, "run_approved")


@pytest.mark.parametrize("field,value", [("args", {"channel": "C999", "text": "See you at 5"}),
                                         ("created", "2099-01-01T00:00:00"), ("server", "gmail")])
def test_changed_after_approval_blocked(modes, jev_scores, mcp_calls, field, value):
    m = modes["work"]
    p = make_item(m, "chg")
    approve_ok(m, "chg")
    meta, body = outbox.read_item(p)
    outbox.write_item(p, {**meta, field: json.dumps(value) if isinstance(value, dict) else value}, body)
    assert "changed after approval" in outbox.execute(m, "chg") or field == "server"
    assert not mcp_calls


def test_body_changed_after_approval_blocked(modes, jev_scores, mcp_calls):
    m = modes["work"]
    p = make_item(m, "chgb")
    approve_ok(m, "chgb")
    meta, _ = outbox.read_item(p)
    outbox.write_item(p, meta, "Something else entirely")
    assert outbox.execute(m, "chgb") == "Blocked chgb: changed after approval"
    assert not mcp_calls


# ---- D16: sending / failed ----

def test_status_sending_written_before_call(modes, jev_scores, mcp_calls):
    m = modes["work"]
    p = make_item(m, "snd")
    seen = []
    mcp_calls.on_call = lambda: seen.append(meta_of(p)["status"])
    approve_ok(m, "snd")
    outbox.execute(m, "snd")
    assert seen == ["sending"]
    assert meta_of(p)["status"] == "sent"


def test_exception_marks_failed_and_blocks_reapprove(modes, jev_scores, mcp_calls):
    m = modes["work"]
    p = make_item(m, "boom")
    mcp_calls.error = TimeoutError("slow")
    approve_ok(m, "boom")
    out = outbox.execute_detailed(m, "boom")
    assert not out.sent and out.reason == "send error: TimeoutError"
    assert meta_of(p)["status"] == "failed"
    with pytest.raises(outbox.Conflict):
        approve_ok(m, "boom")


def test_is_error_marks_failed(modes, jev_scores, mcp_calls):
    m = modes["work"]
    p = make_item(m, "iserr")
    mcp_calls.result = FakeResult(is_error=True, content="nope")
    approve_ok(m, "iserr")
    assert "server error: nope" in outbox.execute(m, "iserr")
    assert meta_of(p)["status"] == "failed"


def test_failed_attempts_count_toward_cap(modes, jev_scores, mcp_calls):
    m = modes["personal"]           # cap 5
    for i in range(m.daily_write_cap):
        ledger.record_attempt("personal", f"f{i}")
    make_item(m, "next")
    approve_ok(m, "next")
    assert outbox.execute(m, "next") == "Blocked next: daily cap reached"


def test_server_must_be_in_repo_mcp_config(modes, jev_scores, mcp_calls):
    m = modes["work"]
    make_item(m, "srv", server="atlassian", tool="post_message")
    approve_ok(m, "srv")
    assert "not an allowed write tool" in outbox.execute(m, "srv")
    assert not mcp_calls


def test_executor_uses_repo_mcp_config_not_vault(modes, jev_scores, mcp_calls):
    m = modes["work"]
    (m.vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"slack": {"url": "https://evil.test/x"}}}),
                                       encoding="utf-8")
    make_item(m, "cfg")
    approve_ok(m, "cfg")
    outbox.execute(m, "cfg")
    assert mcp_calls[0]["conf"]["url"] == "https://slack.example.test/mcp"
    assert mcp_calls[0]["conf"]["headers"]["Authorization"] == "Bearer xoxp-work-secret-1111"


# ---- H1: approvals bind the read-back ----

def test_approve_requires_readback_binding(modes):
    make_item(modes["work"], "nb")
    with pytest.raises(outbox.Conflict, match="read the item first"):
        outbox.approve(modes["work"], "nb")


def test_code_binding(modes):
    m = modes["work"]
    p = make_item(m, "cd")
    code = outbox.readback_code(meta_of(p))
    with pytest.raises(outbox.Conflict, match="wrong code"):
        outbox.approve(m, "cd", code="0" * 8 if code != "0" * 8 else "1" * 8)
    outbox.approve(m, "cd", code=code.upper())
    assert meta_of(p)["status"] == "approved"


# ---- M5: readback code is an HMAC, not a plain sha256 prefix ----

def test_readback_code_stable_across_calls(modes):
    meta = meta_of(make_item(modes["work"], "code1"))
    assert outbox.readback_code(meta) == outbox.readback_code(meta)


def test_readback_code_changes_with_args(modes):
    m = modes["work"]
    a = meta_of(make_item(m, "ca", args={"channel": "C123", "text": "hi"}))
    b = meta_of(make_item(m, "cb", args={"channel": "C123", "text": "bye"}))
    assert outbox.readback_code(a) != outbox.readback_code(b)


def test_readback_code_differs_with_key(modes, home):
    meta = meta_of(make_item(modes["work"], "ck"))
    before = outbox.readback_code(meta)
    (home / ".jarvis" / "approve_code.key").write_bytes(os.urandom(32))
    assert outbox.readback_code(meta) != before


def test_plain_sha256_prefix_code_refused(modes):
    m = modes["work"]
    p = make_item(m, "plain")
    meta = meta_of(p)
    old_scheme_code = outbox.readback_sha256(meta)[:8]
    with pytest.raises(outbox.Conflict, match="wrong code"):
        outbox.approve(m, "plain", code=old_scheme_code)
    assert meta_of(p)["status"] == "pending"


def test_read_card(modes):
    m = modes["work"]
    p = make_item(m, "card")
    card = outbox.read_card(m, "card")
    assert card == f"{outbox.render_readback(meta_of(p))}\ncode {outbox.readback_code(meta_of(p))}"
    make_item(m, "gone", status="sent")
    assert outbox.read_card(m, "gone") == "No approvable item with that id."


# ---- L1 / M4: readback robustness ----

def test_newline_in_value_cannot_fake_a_line():
    rb = outbox.render_readback({"mode": "work", "server": "slack\ntool: evil", "tool": "post_message",
                                 "args": {"text": "hi\nchannel: C_EVIL"}})
    assert rb.splitlines() == ['mode: work', 'server: "slack\\ntool: evil"', 'tool: post_message',
                               'text: "hi\\nchannel: C_EVIL"']


def test_non_string_keys_rejected():
    with pytest.raises(outbox.Blocked, match="keys must be strings"):
        outbox.parse_args({"args": {1: "x"}})


@pytest.mark.parametrize("meta", [{}, {"args": "{bad"}, {"args": {1: 2}}, {"mode": object(), "args": {"a": object()}},
                                  {"args": ["x"]}])
def test_render_readback_never_raises(meta):
    assert isinstance(outbox.render_readback(meta), str)


# ---- M1: shared lock, compare-and-set ----

def test_mode_lock_is_shared_and_reentrant():
    lock = outbox.mode_lock("work")
    assert lock is outbox.mode_lock("work") and lock is not outbox.mode_lock("personal")
    with lock:
        with lock:
            pass


def test_edit_during_checks_blocks_and_is_kept(modes, mcp_calls, monkeypatch):
    m = modes["work"]
    p = make_item(m, "race")
    approve_ok(m, "race")

    def decide(env, state, qs):
        meta, body = outbox.read_item(p)
        outbox.write_item(p, meta, "edited in Obsidian")
        return {k: jev.Decision({"value": 0.0, "confidence": 1.0}) for k in qs}
    monkeypatch.setattr(jev, "decide", decide)
    assert outbox.execute(m, "race") == "Blocked race: changed during checks"
    assert not mcp_calls and outbox.read_item(p)[1] == "edited in Obsidian"


def test_write_item_leaves_no_temp_files(modes):
    p = make_item(modes["work"], "tmp")
    meta, body = outbox.read_item(p)
    outbox.write_item(p, meta, body)
    assert sorted(x.name for x in p.parent.iterdir()) == ["tmp.md"]


# ---- M3 / L3 ----

def test_last_block_redacted_in_vault(modes, jev_scores, mcp_calls):
    m = modes["work"]
    p = make_item(m, "leaky")
    mcp_calls.result = FakeResult(is_error=True, content="Bearer xoxp-work-secret-1111")
    approve_ok(m, "leaky")
    outbox.execute(m, "leaky")
    assert "xoxp-work-secret-1111" not in p.read_text() and "[redacted]" in meta_of(p)["last_block"]


def test_ledger_files_private(modes, jev_scores, mcp_calls, home):
    make_item(modes["personal"], "led")
    jev_scores["hostile"] = 0.9
    approve_ok(modes["personal"], "led")
    outbox.execute(modes["personal"], "led")
    jev_scores["hostile"] = 0.0
    approve_ok(modes["personal"], "led")
    outbox.execute(modes["personal"], "led")
    for name in ("tone_flags.json", "sends.log"):
        assert oct(os.stat(home / ".jarvis" / name).st_mode & 0o777) == "0o600"


def test_tone_flag_then_text_reconfirm(modes, jev_scores, mcp_calls):
    m = modes["personal"]
    p = make_item(m, "tn")
    jev_scores["hostile"] = 0.9
    approve_ok(m, "tn")
    assert outbox.execute(m, "tn").startswith("Blocked tn: tone flag")
    meta, body = outbox.read_item(p)
    assert outbox.tone_flagged(m, meta, body)
    approve_ok(m, "tn")                                  # plain re-approve: still flagged
    assert outbox.execute(m, "tn").startswith("Blocked tn: tone flag")
    approve_ok(m, "tn", reconfirm=True)
    assert outbox.execute(m, "tn") == "Sent tn."
