"""Unit tests for the dead-man's-switch pings (PERF-4).

The guarantees that matter here are behavioral, not network: a ping is a no-op
when the key is unconfigured (safe to wire in before setup), it hits the right
URL (with /fail for a failure), and it NEVER raises — a monitoring ping must not
be able to break the loop it reports on. The `urlopen` call is mocked; no network.
"""

from __future__ import annotations

from agents._lib import heartbeat

KEY = "test-ping-key"


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b""


def test_no_ping_when_key_unconfigured(mocker):
    mocker.patch.object(heartbeat, "_ping_key", return_value=None)
    opener = mocker.patch("urllib.request.urlopen")
    assert heartbeat.ping("cos-briefing") is False
    opener.assert_not_called()  # never touches the network un-armed


def test_success_ping_hits_slug_url(mocker):
    mocker.patch.object(heartbeat, "_ping_key", return_value=KEY)
    opener = mocker.patch("urllib.request.urlopen", return_value=_FakeResp())
    assert heartbeat.ping("cos-briefing") is True
    url = opener.call_args.args[0]
    assert url == f"https://hc-ping.com/{KEY}/cos-briefing"


def test_fail_ping_hits_fail_endpoint(mocker):
    mocker.patch.object(heartbeat, "_ping_key", return_value=KEY)
    opener = mocker.patch("urllib.request.urlopen", return_value=_FakeResp())
    assert heartbeat.ping_fail("cos-briefing") is True
    assert opener.call_args.args[0].endswith("/cos-briefing/fail")


def test_ping_never_raises_on_network_error(mocker):
    mocker.patch.object(heartbeat, "_ping_key", return_value=KEY)
    mocker.patch("urllib.request.urlopen", side_effect=OSError("network down"))
    # Must swallow the error and report False, not propagate it into the loop.
    assert heartbeat.ping("cos-backup") is False


def test_ping_key_missing_keychain_item_is_none(mocker):
    # A missing keychain item surfaces as None (skip), not an exception.
    mocker.patch.object(
        heartbeat.creds, "keychain_get", side_effect=RuntimeError("not found")
    )
    assert heartbeat._ping_key() is None
