"""TLS policy for API-Hub outbound requests.

Requests normally uses certifi instead of the Windows certificate stores. Corporate
roots installed by Group Policy can therefore work in browsers while failing in
API-Hub. Keep certificate and hostname verification enabled, but let Windows
requests use an SSLContext populated from the operating-system trust stores.
"""
from __future__ import annotations

import os
import ssl

import requests
from requests.adapters import HTTPAdapter

from . import config


class _WindowsSystemTrustAdapter(HTTPAdapter):
    """Use Windows' managed CA stores while preserving strict TLS verification."""

    def __init__(self, *args, **kwargs):
        self.system_ssl_context = ssl.create_default_context()
        super().__init__(*args, **kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        if verify is True:
            pool_kwargs["ssl_context"] = self.system_ssl_context
            pool_kwargs["cert_reqs"] = "CERT_REQUIRED"
            pool_kwargs.pop("ca_certs", None)
            pool_kwargs.pop("ca_cert_dir", None)
        return host_params, pool_kwargs


def _uses_windows_system_trust() -> bool:
    return os.name == "nt"


def configure_session(
    session: requests.Session,
    *,
    use_system_trust: bool = False,
) -> requests.Session:
    """Apply TLS policy without changing ordinary interface trust semantics."""
    if config.TLS_CA_BUNDLE:
        session.verify = config.TLS_CA_BUNDLE
        return session

    session.verify = True
    if use_system_trust and _uses_windows_system_trust():
        session.mount("https://", _WindowsSystemTrustAdapter())
    return session
