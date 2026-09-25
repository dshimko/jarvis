"""B2 / D18: CRLF, lone CR, and BOM parse identically to LF; utf-8 throughout."""
from jarvis import outbox
from .conftest import approve_ok, make_item


def test_crlf_parses_identically_to_lf(modes):
    lf = make_item(modes["work"], "lf-item", body="Line one\nLine two")
    lf_meta, lf_body = outbox.read_item(lf)
    crlf = make_item(modes["work"], "lf-item", body="Line one\nLine two", newline="\r\n")
    assert b"\r\n" in crlf.read_bytes()
    assert outbox.read_item(crlf) == (lf_meta, lf_body)


def test_bom_and_lone_cr_parse(modes):
    lf = outbox.read_item(make_item(modes["work"], "x1", body="Hi\nthere"))
    assert outbox.read_item(make_item(modes["work"], "x1", body="Hi\nthere", newline="\r\n", bom=True)) == lf
    assert outbox.read_item(make_item(modes["work"], "x1", body="Hi\nthere", newline="\r")) == lf


def test_crlf_hashes_match_lf(modes):
    m = modes["work"]
    meta, body = outbox.read_item(make_item(m, "h1", body="A\nB"))
    meta2, body2 = outbox.read_item(make_item(m, "h1", body="A\nB", newline="\r\n", bom=True, created=meta["created"]))
    assert outbox.body_sha256(body) == outbox.body_sha256(body2)
    assert outbox.readback_sha256(meta) == outbox.readback_sha256(meta2)


def test_crlf_item_approves_and_sends(modes, jev_scores, mcp_calls):
    m = modes["work"]
    make_item(m, "crlf-send", newline="\r\n", bom=True)
    approve_ok(m, "crlf-send")
    assert outbox.execute(m, "crlf-send") == "Sent crlf-send."
    assert mcp_calls[0]["args"] == {"channel": "C123", "text": "See you at 5"}


def test_utf8_round_trip(modes):
    m = modes["work"]
    p = make_item(m, "utf", body="Café — naïve ✓")
    meta, body = outbox.read_item(p)
    outbox.write_item(p, meta, body)
    assert outbox.read_item(p)[1] == "Café — naïve ✓"
    assert "Café".encode("utf-8") in p.read_bytes()


def test_malformed_item_is_blocked(modes):
    p = modes["work"].vault / "outbox" / "bad.md"
    p.write_text("no front matter", encoding="utf-8")
    assert outbox.execute(modes["work"], "bad").startswith("Blocked bad: malformed")
