"""Every state-changing request path carries a CSRF token.

Static checks over templates (cover pages no other test renders) plus configuration checks.
PR #41 fixed ops pages that posted via wcPost but never set the csrftoken cookie.
"""

import re
from pathlib import Path

from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver

TEMPLATES_DIR = Path(settings.BASE_DIR) / "templates"
STATIC_JS_DIR = Path(settings.BASE_DIR) / "static" / "js"

# Renders the token in <meta name="csrf-token">, which also sets the csrftoken cookie
TOKEN_BASE_TEMPLATE = "admin/base_site.html"

# View callbacks allowed to skip CSRF, with the reason. Keep empty unless unavoidable.
CSRF_EXEMPT_ALLOWLIST: dict[str, str] = {}

EXTENDS_RE = re.compile(r"""{%\s*extends\s+["']([^"']+)["']\s*%}""")
JS_POST_RE = re.compile(r"\b(wcPost|wcStream)\s*\(|method\s*:\s*['\"]POST['\"]", re.IGNORECASE)
# Whole fetch(...) statement, so headers after `method` are seen; filtered to POSTs below
FETCH_CALL_RE = re.compile(r"fetch\([^;]*;", re.DOTALL)
POST_METHOD_RE = re.compile(r"method\s*:\s*['\"]POST['\"]", re.IGNORECASE)


def _templates() -> list[Path]:
    return sorted(TEMPLATES_DIR.rglob("*.html"))


def _rel(path: Path) -> str:
    return path.relative_to(TEMPLATES_DIR).as_posix()


def _post_form_blocks(source: str) -> list[tuple[int, str]]:
    """(line, body) for each <form method="post"> and <c-form> (always POST) in a template."""
    blocks = []
    for opening_re, close in (
        (re.compile(r"<form\b[^>]*>", re.IGNORECASE), "</form>"),
        (re.compile(r"<c-form\b[^>]*>", re.IGNORECASE), "</c-form>"),
    ):
        for match in opening_re.finditer(source):
            if close == "</form>" and not re.search(r"""method\s*=\s*["']post""", match.group(0), re.IGNORECASE):
                continue
            end = source.find(close, match.end())
            body = source[match.end() : end if end != -1 else len(source)]
            blocks.append((source.count("\n", 0, match.start()) + 1, body))
    return blocks


def _missing_form_tokens(files: dict[str, str]) -> list[str]:
    missing = []
    for name, source in files.items():
        if name == "cotton/form.html":  # component definition; callers put the token in its slot
            continue
        for line, body in _post_form_blocks(source):
            if "csrf_token" not in body:
                missing.append(f"{name}:{line}")
    return missing


def _extends_chain(name: str, files: dict[str, str]) -> list[str]:
    chain = [name]
    while (source := files.get(chain[-1])) and (match := EXTENDS_RE.search(source)):
        parent = match.group(1)
        if parent in chain:
            break
        chain.append(parent)
    return chain


def _js_post_problems(files: dict[str, str]) -> list[str]:
    problems = []
    for name, source in files.items():
        if not JS_POST_RE.search(source):
            continue
        if TOKEN_BASE_TEMPLATE not in _extends_chain(name, files):
            problems.append(f"{name}: posts from JavaScript but doesn't extend {TOKEN_BASE_TEMPLATE}")
        for match in _fetch_posts(source):
            if "X-CSRFToken" not in match.group(0):
                line = source.count("\n", 0, match.start()) + 1
                problems.append(f"{name}:{line}: fetch POST without an X-CSRFToken header")
    return problems


def _fetch_posts(source: str) -> list[re.Match[str]]:
    return [m for m in FETCH_CALL_RE.finditer(source) if POST_METHOD_RE.search(m.group(0))]


def _load_templates() -> dict[str, str]:
    return {_rel(path): path.read_text() for path in _templates()}


def _all_patterns(resolver: URLResolver, prefix: str = "") -> list[tuple[str, URLPattern]]:
    patterns = []
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            patterns += _all_patterns(entry, prefix + str(entry.pattern))
        else:
            patterns.append((prefix + str(entry.pattern), entry))
    return patterns


# --- The checks ---------------------------------------------------------------------------------


def test_every_post_form_has_csrf_token():
    missing = _missing_form_tokens(_load_templates())
    assert not missing, f"POST forms without {{% csrf_token %}}: {missing}"


def test_javascript_posts_can_find_the_token():
    problems = _js_post_problems(_load_templates())
    assert not problems, problems


def test_static_js_fetch_posts_send_csrf_header():
    problems = [
        f"{path.name}:{js.count(chr(10), 0, m.start()) + 1}"
        for path in STATIC_JS_DIR.glob("*.js")
        for js in [path.read_text()]
        for m in _fetch_posts(js)
        if "X-CSRFToken" not in m.group(0)
    ]
    assert not problems, f"fetch POST without X-CSRFToken: {problems}"


def test_token_base_template_renders_token():
    assert "{{ csrf_token }}" in (TEMPLATES_DIR / TOKEN_BASE_TEMPLATE).read_text()


def test_no_unexpected_csrf_exempt_views():
    exempt = {}
    for route, pattern in _all_patterns(get_resolver()):
        callback = pattern.callback
        if getattr(callback, "csrf_exempt", False):
            exempt[f"{callback.__module__}.{getattr(callback, '__qualname__', callback)}"] = route
    unexpected = {view: route for view, route in exempt.items() if view not in CSRF_EXEMPT_ALLOWLIST}
    assert not unexpected, f"csrf_exempt views not in CSRF_EXEMPT_ALLOWLIST: {unexpected}"


def test_csrf_middleware_enabled():
    assert "django.middleware.csrf.CsrfViewMiddleware" in settings.MIDDLEWARE


# --- The checks must catch what they claim to --------------------------------------------------


def test_checker_flags_form_without_token():
    files = {"x.html": '<form method="post"><input name="a"></form>', "y.html": "<c-form><input></c-form>"}
    assert _missing_form_tokens(files) == ["x.html:1", "y.html:1"]


def test_checker_accepts_form_with_token_and_ignores_get_forms():
    files = {"x.html": '<form method="POST">{% csrf_token %}</form><form method="get"></form>'}
    assert _missing_form_tokens(files) == []


def test_checker_flags_js_post_page_outside_token_base():
    files = {
        "admin/base_site.html": "{{ csrf_token }}",
        "ok.html": '{% extends "mid.html" %} wcPost(url, {})',
        "mid.html": '{% extends "admin/base_site.html" %}',
        "bad.html": '{% extends "standalone.html" %} wcPost(url, {})',
        "standalone.html": "<html></html>",
    }
    assert _js_post_problems(files) == ["bad.html: posts from JavaScript but doesn't extend admin/base_site.html"]


def test_checker_flags_raw_fetch_post_without_header():
    files = {
        "admin/base_site.html": "",
        "p.html": '{% extends "admin/base_site.html" %} fetch(u, { method: "POST", body: b });',
    }
    assert _js_post_problems(files) == ["p.html:1: fetch POST without an X-CSRFToken header"]


def test_checker_sees_header_after_method():
    """Real pages put headers after `method`; the check must read the whole call."""
    files = {
        "admin/base_site.html": "",
        "p.html": '{% extends "admin/base_site.html" %}\n'
        "fetch(u, {\n method: 'POST',\n headers: {'X-CSRFToken': getCSRFToken()},\n body: b\n});",
    }
    assert _js_post_problems(files) == []
