"""Messages render once, as alerts, from the base template."""

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils.safestring import mark_safe

pytestmark = pytest.mark.django_db


def _render(queue: Callable[[HttpRequest], Any]) -> str:
    request = RequestFactory().get("/")
    request.session = {}  # type: ignore[assignment]
    request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
    request.user = User(username="tester")
    queue(request)
    return render_to_string("admin/base_site.html", request=request)


@pytest.mark.parametrize(
    ("add", "variant"),
    [
        (messages.error, "alert--error"),
        (messages.warning, "alert--warning"),
        (messages.success, "alert--success"),
        (messages.info, "alert--info"),
    ],
)
def test_message_renders_as_matching_alert(add: Callable[..., Any], variant: str) -> None:
    html = _render(lambda r: add(r, "PROBE-TEXT"))
    assert variant in html
    assert "PROBE-TEXT" in html
    assert "messagelist" not in html


def test_message_appears_exactly_once() -> None:
    html = _render(lambda r: messages.error(r, "PROBE-TEXT"))
    assert html.count("PROBE-TEXT") == 1


def test_several_messages_render_in_order() -> None:
    def queue(r: HttpRequest) -> None:
        messages.error(r, "FIRST-PROBE")
        messages.success(r, "SECOND-PROBE")

    html = _render(queue)
    assert html.index("FIRST-PROBE") < html.index("SECOND-PROBE")
    assert html.count("FIRST-PROBE") == html.count("SECOND-PROBE") == 1


def test_message_html_is_escaped() -> None:
    html = _render(lambda r: messages.error(r, "<b>PROBE</b>"))
    assert "&lt;b&gt;PROBE&lt;/b&gt;" in html
    assert "<b>PROBE</b>" not in html


def test_safe_message_keeps_markup() -> None:
    html = _render(lambda r: messages.error(r, mark_safe("<b>PROBE</b>")))
    assert "<b>PROBE</b>" in html


def test_no_messages_renders_no_alert() -> None:
    html = _render(lambda r: None)
    assert 'role="alert"' not in html
