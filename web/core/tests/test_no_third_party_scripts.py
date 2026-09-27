"""Front-end libraries are served from our own origin, not a CDN (security review, Medium)."""

import re

import pytest
from django.test import Client
from django.urls import reverse

from core.middleware import SecurityHeadersMiddleware

pytestmark = pytest.mark.django_db


def test_pages_load_no_third_party_scripts(admin_user):
    client = Client()
    client.force_login(admin_user)

    html = client.get(reverse("ticket_list")).content.decode()

    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert srcs, "expected script tags"
    external = [s for s in srcs if s.startswith(("http://", "https://", "//"))]
    assert external == [], external


def test_vendored_scripts_carry_integrity(admin_user):
    client = Client()
    client.force_login(admin_user)

    html = client.get(reverse("ticket_list")).content.decode()

    for name in ("alpinejs-csp", "alpinejs-collapse", "htmx"):
        tag = re.search(rf'<script[^>]*src="[^"]*vendor/{name}[^"]*"[^>]*>', html)
        assert tag and 'integrity="sha384-' in tag.group(0), name


def test_csp_does_not_allow_unpkg(rf):
    response = SecurityHeadersMiddleware(lambda request: __import__("django.http").http.HttpResponse())(rf.get("/"))

    assert "unpkg.com" not in response["Content-Security-Policy"]
