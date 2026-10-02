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
    context = {"event_name": "Fall & Winter", "school_name": "A & B <College>", "team_number": 7}

    subject = render_to_string("emails/scorecard_subject.txt", context).strip()
    text = render_to_string("emails/scorecard.txt", context)

    assert subject == "WCComps Score Card - Fall & Winter - A & B <College>"
    assert "Fall & Winter" in text
    assert "&amp;" not in text
