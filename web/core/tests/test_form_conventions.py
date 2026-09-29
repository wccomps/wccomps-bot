"""Static checks that keep page templates on the shared form and message conventions.

Exceptions must be visible in this file, with a reason, so they get reviewed.
"""

import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def all_page_templates() -> list[Path]:
    """Every template except cotton components and email bodies."""
    return sorted(p for p in TEMPLATES_DIR.rglob("*.html") if "cotton" not in p.parts and "emails" not in p.parts)


def rel(path: Path) -> str:
    return str(path.relative_to(TEMPLATES_DIR))


MESSAGE_LOOP = re.compile(r"{%\s*if\s+messages\s*%}|{%\s*for\s+\w+\s+in\s+messages\s*%}")

# Files allowed to loop over messages themselves, with the reason.
MESSAGE_LOOP_ALLOWLIST = {
    "admin/base_site.html": "the one place messages render",
    "registration/register.html": "standalone public page that extends no base; has its own message styling",
}


def test_pages_do_not_render_messages_themselves() -> None:
    """The base template renders messages; a page loop would show every message twice."""
    offenders = [
        rel(p)
        for p in all_page_templates()
        if rel(p) not in MESSAGE_LOOP_ALLOWLIST and MESSAGE_LOOP.search(p.read_text())
    ]
    if offenders:
        pytest.fail(
            "Templates loop over messages; delete the loop (admin/base_site.html renders them):\n"
            + "\n".join(f"  - {t}" for t in offenders)
        )
