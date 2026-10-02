"""Unit tests for the gateway and tunnel dead-man's switches
(PRD-liveness-alerting §2). No network: `urlopen` and the pings are mocked."""

from __future__ import annotations

from agents.gateway import liveness


class _Resp:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"status":"ok"}'


def test_health_ok_true_on_200(mocker):
    opener = mocker.patch("urllib.request.urlopen", return_value=_Resp(200))
    assert liveness.health_ok("http://127.0.0.1:8788/") is True
    assert opener.call_args.args[0].full_url == "http://127.0.0.1:8788/health"


def test_health_ok_false_on_error_never_raises(mocker):
    mocker.patch("urllib.request.urlopen", side_effect=OSError("refused"))
    assert liveness.health_ok("http://127.0.0.1:8788") is False


def test_health_ok_false_on_non_200(mocker):
    mocker.patch("urllib.request.urlopen", return_value=_Resp(503))
    assert liveness.health_ok("http://127.0.0.1:8788") is False


def test_both_paths_up_pings_both(mocker):
    mocker.patch.object(liveness, "health_ok", return_value=True)
    mocker.patch.object(liveness, "public_url", return_value="https://example.test")
    ping = mocker.patch("agents.gateway.liveness.heartbeat.ping")
    result = liveness.Prober("http://127.0.0.1:8788").beat()
    assert result == {"cos-gateway": True, "cos-tunnel": True}
    assert [c.args[0] for c in ping.call_args_list] == ["cos-gateway", "cos-tunnel"]


def test_tunnel_down_pings_gateway_only(mocker):
    mocker.patch.object(
        liveness, "health_ok", side_effect=lambda url: url.startswith("http://127")
    )
    mocker.patch.object(liveness, "public_url", return_value="https://example.test")
    ping = mocker.patch("agents.gateway.liveness.heartbeat.ping")
    liveness.Prober("http://127.0.0.1:8788").beat()
    ping.assert_called_once_with("cos-gateway")


def test_gateway_down_pings_nothing(mocker):
    mocker.patch.object(liveness, "health_ok", return_value=False)
    mocker.patch.object(liveness, "public_url", return_value="https://example.test")
    ping = mocker.patch("agents.gateway.liveness.heartbeat.ping")
    liveness.Prober("http://127.0.0.1:8788").beat()
    ping.assert_not_called()


def test_no_public_url_skips_tunnel_and_warns_once(mocker, caplog):
    mocker.patch.object(liveness, "health_ok", return_value=True)
    mocker.patch.object(liveness, "public_url", return_value=None)
    ping = mocker.patch("agents.gateway.liveness.heartbeat.ping")
    prober = liveness.Prober("http://127.0.0.1:8788")
    with caplog.at_level("WARNING"):
        prober.beat()
        prober.beat()
    ping.assert_called_with("cos-gateway")
    assert "cos-tunnel" not in [c.args[0] for c in ping.call_args_list]
    assert sum("un-armed" in r.message for r in caplog.records) == 1


def test_public_url_missing_item_is_none(mocker):
    mocker.patch.object(liveness.creds, "keychain_get", side_effect=RuntimeError("missing"))
    assert liveness.public_url() is None


def test_prober_survives_a_raising_probe(mocker):
    # The thread loop catches anything beat() raises; beat itself relies on
    # health_ok never raising. Pin that contract.
    mocker.patch("urllib.request.urlopen", side_effect=ValueError("bad url"))
    mocker.patch.object(liveness, "public_url", return_value="not a url")
    ping = mocker.patch("agents.gateway.liveness.heartbeat.ping")
    assert liveness.Prober("http://127.0.0.1:8788").beat() == {
        "cos-gateway": False, "cos-tunnel": False}
    ping.assert_not_called()
