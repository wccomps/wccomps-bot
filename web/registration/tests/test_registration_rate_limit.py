"""Registration rate limit counts per real client, shared across workers (security review, Medium)."""

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from core.utils import client_ip
from registration.models import RegistrationRateLimit

pytestmark = pytest.mark.django_db

PROXY = "10.42.3.17"  # REMOTE_ADDR in production is the proxy in front of the app, never the client


def test_client_ip_prefers_cloudflare_header():
    request = RequestFactory().get("/", REMOTE_ADDR=PROXY, HTTP_CF_CONNECTING_IP="203.0.113.7")
    assert client_ip(request) == "203.0.113.7"


@pytest.mark.parametrize("header", ["", "not-an-ip", "203.0.113.7, 10.0.0.1"])
def test_client_ip_falls_back_on_missing_or_malformed_header(header):
    request = RequestFactory().get("/", REMOTE_ADDR=PROXY, HTTP_CF_CONNECTING_IP=header)
    assert client_ip(request) == PROXY


def test_ipv6_client_supported():
    request = RequestFactory().get("/", REMOTE_ADDR=PROXY, HTTP_CF_CONNECTING_IP="2001:db8::5")
    assert client_ip(request) == "2001:db8::5"


def _register(ip: str):
    return Client().post(reverse("registration_register"), {}, REMOTE_ADDR=PROXY, HTTP_CF_CONNECTING_IP=ip)


def test_limit_is_per_client_not_per_proxy():
    for _ in range(10):
        RegistrationRateLimit.objects.create(ip="203.0.113.7")

    blocked = _register("203.0.113.7")
    other = _register("198.51.100.9")  # same proxy REMOTE_ADDR, different real client

    assert blocked.status_code == 429
    assert other.status_code != 429


def test_counter_is_shared_database_state_not_process_memory():
    """Every gunicorn worker sees the same count (the old LocMemCache counter was per worker)."""
    RegistrationRateLimit.objects.bulk_create([RegistrationRateLimit(ip="203.0.113.7") for _ in range(10)])

    assert RegistrationRateLimit.over_limit("203.0.113.7")
    assert not RegistrationRateLimit.over_limit("198.51.100.9")


def test_old_attempts_do_not_count():
    from datetime import timedelta

    from django.utils import timezone

    for _ in range(10):
        row = RegistrationRateLimit.objects.create(ip="203.0.113.7")
        RegistrationRateLimit.objects.filter(pk=row.pk).update(attempted_at=timezone.now() - timedelta(hours=2))

    assert not RegistrationRateLimit.over_limit("203.0.113.7")
