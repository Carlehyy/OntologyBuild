import ssl

import requests
from requests.adapters import HTTPAdapter

from app.api_hub import config, credential, executor, tls


def test_explicit_ca_bundle_has_priority(monkeypatch):
    monkeypatch.setattr(config, "TLS_CA_BUNDLE", "/managed/ca.pem")
    monkeypatch.setattr(tls, "_uses_windows_system_trust", lambda: True)

    session = tls.configure_session(
        requests.Session(), use_system_trust=True
    )

    assert session.verify == "/managed/ca.pem"
    assert type(session.get_adapter("https://")) is HTTPAdapter


def test_regular_interface_keeps_requests_default_trust_on_windows(monkeypatch):
    monkeypatch.setattr(config, "TLS_CA_BUNDLE", "")
    monkeypatch.setattr(tls, "_uses_windows_system_trust", lambda: True)

    session = tls.configure_session(requests.Session())

    assert session.verify is True
    assert type(session.get_adapter("https://")) is HTTPAdapter


def test_non_windows_w3_keeps_requests_default_trust(monkeypatch):
    monkeypatch.setattr(config, "TLS_CA_BUNDLE", "")
    monkeypatch.setattr(tls, "_uses_windows_system_trust", lambda: False)

    session = tls.configure_session(
        requests.Session(), use_system_trust=True
    )

    assert session.verify is True
    assert type(session.get_adapter("https://")) is HTTPAdapter


def test_windows_w3_uses_system_context_with_strict_verification(monkeypatch):
    monkeypatch.setattr(config, "TLS_CA_BUNDLE", "")
    monkeypatch.setattr(tls, "_uses_windows_system_trust", lambda: True)

    session = tls.configure_session(
        requests.Session(), use_system_trust=True
    )
    adapter = session.get_adapter("https://")
    assert isinstance(adapter, tls._WindowsSystemTrustAdapter)

    prepared = requests.Request("GET", "https://his.huawei.com/path").prepare()
    host_params, pool_kwargs = adapter.build_connection_pool_key_attributes(
        prepared, session.verify
    )

    assert host_params["host"] == "his.huawei.com"
    assert pool_kwargs["ssl_context"] is adapter.system_ssl_context
    assert pool_kwargs["cert_reqs"] == "CERT_REQUIRED"
    assert adapter.system_ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert adapter.system_ssl_context.check_hostname is True
    assert "ca_certs" not in pool_kwargs
    assert "ca_cert_dir" not in pool_kwargs


def test_w3_session_opts_into_system_trust(monkeypatch):
    observed = {}

    def fake_configure(session, **kwargs):
        observed.update(kwargs)
        return session

    monkeypatch.setattr(tls, "configure_session", fake_configure)

    credential._new_session()

    assert observed == {"use_system_trust": True}


def test_regular_interface_session_does_not_opt_into_system_trust(monkeypatch):
    observed = {}

    def fake_configure(session, **kwargs):
        observed.update(kwargs)
        return session

    monkeypatch.setattr(tls, "configure_session", fake_configure)

    session = executor._session_for_request(False, {})

    assert session is not None
    assert observed == {}
