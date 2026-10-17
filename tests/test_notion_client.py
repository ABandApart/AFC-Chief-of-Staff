"""Unit tests for the Notion client (pure — no network, no keychain)."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from agents._lib import notion_client as nc

# --- fakes ------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(status, payload=None, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    body = io.BytesIO(json.dumps(payload or {}).encode())
    return urllib.error.HTTPError("https://api.notion.com/v1/x", status, "err", headers, body)


class _Script:
    """An opener that replays a scripted sequence of responses/errors."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return _Resp(step)


def _client(opener, sleeps=None, clock=None):
    sleeps = [] if sleeps is None else sleeps
    return nc.NotionClient("ntn_test", _opener=opener, _sleep=sleeps.append,
                           _clock=clock or (lambda: 1000.0))


# --- request shaping ----------------------------------------------------------------


def test_build_request_headers_and_body():
    req = nc.build_request("POST", "/search", token="ntn_x", body={"query": "a"})
    assert req.full_url == "https://api.notion.com/v1/search"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer ntn_x"
    assert req.get_header("Notion-version") == "2026-03-11"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"query": "a"}


def test_build_request_get_has_no_body_or_content_type():
    req = nc.build_request("GET", "/users/me", token="ntn_x")
    assert req.data is None
    assert req.get_header("Content-type") is None


def test_version_is_pinned():
    # A version bump must be a deliberate code change (see the module docstring).
    assert nc.NOTION_VERSION == "2026-03-11"


# --- retry --------------------------------------------------------------------------


def test_retry_delay_honors_retry_after_floor():
    assert nc.retry_delay("7", 0, _jitter=lambda: 0.0) == 7.0
    assert nc.retry_delay("7", 1, _jitter=lambda: 0.0) == 7.0


def test_retry_delay_backs_off_exponentially_on_repeats():
    assert nc.retry_delay(None, 0, _jitter=lambda: 0.0) == 1.0
    assert nc.retry_delay(None, 2, _jitter=lambda: 0.0) == 4.0
    assert nc.retry_delay(None, 3, _jitter=lambda: 0.5) == 12.0


def test_retry_delay_ignores_unparseable_header():
    assert nc.retry_delay("soon", 0, _jitter=lambda: 0.0) == 1.0


@pytest.mark.parametrize("status", [429, 529])
def test_request_retries_rate_limits_then_succeeds(status):
    opener = _Script(_http_error(status, retry_after="2"), {"ok": True})
    sleeps = []
    assert _client(opener, sleeps).request("GET", "/users/me") == {"ok": True}
    assert 2.0 in sleeps
    assert len(opener.requests) == 2


def test_request_gives_up_after_max_retries():
    opener = _Script(*[_http_error(429, retry_after="1") for _ in range(nc.MAX_RETRIES)])
    with pytest.raises(nc.NotionError) as exc:
        _client(opener).request("GET", "/users/me")
    assert exc.value.status == 429


def test_request_does_not_retry_auth_errors_and_carries_code():
    opener = _Script(_http_error(401, {"code": "unauthorized", "message": "API token is invalid."}))
    with pytest.raises(nc.NotionError) as exc:
        _client(opener).request("GET", "/users/me")
    assert exc.value.status == 401
    assert exc.value.code == "unauthorized"
    assert "API token is invalid." in str(exc.value)
    assert len(opener.requests) == 1


def test_transport_error_becomes_notion_error():
    opener = _Script(urllib.error.URLError("no route"))
    with pytest.raises(nc.NotionError):
        _client(opener).request("GET", "/users/me")


# --- throttle -----------------------------------------------------------------------


def test_throttle_spaces_back_to_back_requests():
    t = [100.0]
    sleeps = []

    def clock():
        return t[0]

    def sleep(s):
        sleeps.append(s)
        t[0] += s

    client = nc.NotionClient("ntn_test", _opener=_Script({}, {}), _sleep=sleep, _clock=clock)
    client.request("GET", "/a")
    client.request("GET", "/b")
    assert sleeps == [pytest.approx(nc.MIN_INTERVAL)]


# --- search -------------------------------------------------------------------------


def test_iter_search_follows_cursor_and_sets_filter():
    opener = _Script(
        {"results": [{"id": "1"}], "has_more": True, "next_cursor": "c2"},
        {"results": [{"id": "2"}], "has_more": False, "next_cursor": None},
    )
    ids = [r["id"] for r in _client(opener).iter_search(object_type="page")]
    assert ids == ["1", "2"]
    first, second = (json.loads(r.data) for r in opener.requests)
    assert first["filter"] == {"property": "object", "value": "page"}
    assert "start_cursor" not in first
    assert second["start_cursor"] == "c2"


# --- titles and hashes ----------------------------------------------------------------


def test_title_of_page_uses_title_property():
    page = {"object": "page", "properties": {
        "Status": {"type": "select"},
        "Name": {"type": "title", "title": [{"plain_text": "AFC Richmond "},
                                            {"plain_text": "Chief-of-Staff"}]},
    }}
    assert nc.title_of(page) == "AFC Richmond Chief-of-Staff"


def test_title_of_data_source_uses_top_level_title():
    source = {"object": "data_source", "title": [{"plain_text": "Key Results"}]}
    assert nc.title_of(source) == "Key Results"


def test_title_of_untitled():
    assert nc.title_of({"object": "page", "properties": {}}) == "(untitled)"


def test_content_hash_ignores_key_order():
    assert nc.content_hash({"a": 1, "b": [1, 2]}) == nc.content_hash({"b": [1, 2], "a": 1})


def test_content_hash_changes_with_content():
    assert nc.content_hash({"a": 1}) != nc.content_hash({"a": 2})
