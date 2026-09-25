"""SSE line parser: event/data frames, the heartbeat comment, and tolerance of unknown event
types and malformed JSON."""
from jarvis_client.api import SSEEvent, parse_sse_stream


def test_parses_event_and_data_frames():
    lines = [
        ": heartbeat",
        "",
        "event: pending_new",
        'data: {"mode": "work", "id": "abc", "first_line": "hi", "obsidian_uri": "obsidian://x"}',
        "",
        "event: item_blocked",
        'data: {"mode": "personal", "id": "x", "reason": "tone flag"}',
        "",
    ]

    events = list(parse_sse_stream(lines))

    assert events == [
        SSEEvent("pending_new", {"mode": "work", "id": "abc", "first_line": "hi",
                                  "obsidian_uri": "obsidian://x"}),
        SSEEvent("item_blocked", {"mode": "personal", "id": "x", "reason": "tone flag"}),
    ]


def test_comment_only_and_blank_frames_are_ignored():
    lines = [": heartbeat", "", ": heartbeat", ""]

    assert list(parse_sse_stream(lines)) == []


def test_multi_line_data_is_joined_before_parsing():
    lines = ["event: schedule_done", 'data: {"mode": "work",', 'data: "job": "daily-review",', 'data: "ok": true}', ""]

    events = list(parse_sse_stream(lines))

    assert events == [SSEEvent("schedule_done", {"mode": "work", "job": "daily-review", "ok": True})]


def test_unknown_event_type_is_tolerated():
    lines = ["event: some_future_event", 'data: {"mode": "work"}', ""]

    events = list(parse_sse_stream(lines))

    assert events == [SSEEvent("some_future_event", {"mode": "work"})]


def test_malformed_json_yields_an_empty_payload_instead_of_raising():
    lines = ["event: pending_new", "data: {not json", ""]

    events = list(parse_sse_stream(lines))

    assert events == [SSEEvent("pending_new", {})]
