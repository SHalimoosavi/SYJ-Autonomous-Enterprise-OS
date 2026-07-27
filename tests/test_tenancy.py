"""Verifies tenant context resolution and rejection of un-scoped requests."""
import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_does_not_require_tenant():
    resp = client.get("/health")
    assert resp.status_code == 200


def test_api_endpoint_requires_tenant_header():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 400


def test_api_endpoint_accepts_tenant_header():
    resp = client.get("/api/v1/health", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 200


def test_bare_ip_host_is_not_treated_as_a_subdomain_tenant():
    """127.0.0.1:PORT has 4 dot-separated labels; must not be misread as a tenant."""
    resp = client.get("/api/v1/health", headers={"Host": "127.0.0.1:8123"})
    assert resp.status_code == 400
