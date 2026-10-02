"""Plain-text emails show values as typed; only their HTML parts escape them."""

from types import SimpleNamespace

from django.template.loader import render_to_string

RAW = "Horse-&123-<Battery>"


def test_packet_email_text_shows_the_password_as_typed():
    context = {
        "team": SimpleNamespace(team_number=7),
        "packet": SimpleNamespace(title="Packet & Rules"),
        "username": "team07",
        "password": RAW,
        "team_extras": {"API Key": RAW},
    }

    text = render_to_string("packets/emails/packet_notification.txt", context)
    html = render_to_string("packets/emails/packet_notification.html", context)

    assert f"Password: {RAW}" in text
    assert "Packet & Rules" in text
    assert "&amp;" not in text
    assert "Horse-&amp;123-&lt;Battery&gt;" in html


def test_scorecard_email_text_and_subject_show_names_as_typed():
    context = {"school_name": "A & B <College>", "team_number": 7}

    subject = render_to_string("emails/scorecard_subject.txt", context).strip()
    text = render_to_string("emails/scorecard.txt", context)

    assert subject == "WCComps Score Card - A & B <College>"
    assert "A & B <College>" in text
    assert "&amp;" not in text


def test_scorecard_email_has_no_blank_event_name():
    """Quotient never supplies an event name; the email used to read "participating in !" and "Card -  - "."""
    context = {"school_name": "Example University", "team_number": 7}

    subject = render_to_string("emails/scorecard_subject.txt", context).strip()
    text = render_to_string("emails/scorecard.txt", context)
    html = render_to_string("emails/scorecard.html", context)

    assert subject == "WCComps Score Card - Example University"
    for body in (text, html):
        assert "Thank you for participating!" in body
        assert " in !" not in body
    assert "Event:" not in text
