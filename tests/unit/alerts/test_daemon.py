from popctl.alerts.config import AlertsConfig
from popctl.alerts.daemon import build_url


def test_build_url_adds_client_id_and_token():
    url = build_url(
        AlertsConfig(ws_url="ws://alert-host.test:8765/", token="sek", client_id="desktop-alerts")
    )
    assert url.startswith("ws://alert-host.test:8765/?")
    assert "client_id=desktop-alerts" in url
    assert "token=sek" in url


def test_build_url_omits_empty_token():
    url = build_url(AlertsConfig(ws_url="ws://alert-host.test:8765/"))
    assert "token=" not in url
    assert "client_id=desktop-alerts" in url


def test_build_url_respects_existing_query():
    url = build_url(AlertsConfig(ws_url="ws://alert-host.test:8765/ws?foo=bar"))
    assert "?foo=bar&" in url
    assert "client_id=desktop-alerts" in url
