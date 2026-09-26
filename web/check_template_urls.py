#!/usr/bin/env python
"""Check hardcoded URLs in templates against Django URL configuration."""

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")

import django

django.setup()

from django.urls import URLPattern, URLResolver, get_resolver


def get_all_url_patterns(resolver: URLResolver | None = None, prefix: str = "") -> list[str]:
    """Extract all URL patterns from Django's URL configuration."""
    if resolver is None:
        resolver = get_resolver()

    patterns: list[str] = []
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            new_prefix = prefix + str(pattern.pattern)
            patterns.extend(get_all_url_patterns(pattern, new_prefix))
        elif isinstance(pattern, URLPattern):
            full_pattern = prefix + str(pattern.pattern)
            patterns.append(full_pattern)
    return patterns


def pattern_to_regex(django_pattern: str) -> str:
    """Convert Django URL pattern to regex for matching."""
    regex = django_pattern
    regex = re.sub(r"<int:\w+>", r"\\d+", regex)
    regex = re.sub(r"<str:\w+>", r"[^/]+", regex)
    regex = re.sub(r"<slug:\w+>", r"[\\w-]+", regex)
    regex = re.sub(r"<uuid:\w+>", r"[0-9a-f-]+", regex)
    regex = re.sub(r"<path:\w+>", r".+", regex)
    regex = re.sub(r"<\w+>", r"[^/]+", regex)
    return f"^/{regex}$"


def normalize_template_url(url: str) -> str:
    """Normalize a template URL by replacing Django variables with placeholders."""
    url = url.split("?")[0]
    url = re.sub(r"\{\{[^}]+\}\}", "PLACEHOLDER", url)
    return url


def url_matches_any_pattern(url: str, pattern_regexes: list[re.Pattern[str]]) -> bool:
    """Check if URL matches any Django URL pattern."""
    normalized = normalize_template_url(url)

    for pattern_re in pattern_regexes:
        if pattern_re.match(normalized.replace("PLACEHOLDER", "123")):
            return True
        if pattern_re.match(normalized.replace("PLACEHOLDER", "test")):
            return True
    return False


def extract_urls_from_template(filepath: Path) -> list[tuple[int, str]]:
    """Extract hardcoded URLs from a template file."""
    urls: list[tuple[int, str]] = []
    content = filepath.read_text()

    pattern = r'(?:href|action|src)=["\'](/[^"\']*)["\']'

    for i, line in enumerate(content.split("\n"), 1):
        if "{#" in line and "#}" in line:
            continue
        for match in re.finditer(pattern, line):
            url = match.group(1)
            if url.startswith("{{"):
                continue
            if url.startswith("/static/"):
                continue
            urls.append((i, url))

    return urls


def main() -> None:
    templates_dir = Path(__file__).parent / "templates"
    if not templates_dir.exists():
        print(f"Templates directory not found: {templates_dir}")
        sys.exit(1)

    django_patterns = get_all_url_patterns()
    pattern_regexes: list[re.Pattern[str]] = []
    for p in django_patterns:
        try:
            regex = pattern_to_regex(p)
            pattern_regexes.append(re.compile(regex))
        except re.error:
            pass

    known_valid = [
        re.compile(r"^/admin/.*$"),
        re.compile(r"^/accounts/.*$"),
    ]
    pattern_regexes.extend(known_valid)

    errors: list[tuple[Path, int, str]] = []
    warnings: list[tuple[Path, int, str]] = []

    for template_file in templates_dir.rglob("*.html"):
        urls = extract_urls_from_template(template_file)
        rel_path = template_file.relative_to(templates_dir)

        for line_num, url in urls:
            if not url_matches_any_pattern(url, pattern_regexes):
                errors.append((rel_path, line_num, url))
            elif "{{" not in url and "{%" not in url:
                warnings.append((rel_path, line_num, url))

    if errors:
        print("ERRORS - URLs that don't match any pattern:")
        for rel_path, line_num, url in errors:
            print(f"  {rel_path}:{line_num}: {url}")
        print()

    if warnings:
        print("WARNINGS - Hardcoded URLs (consider using {% url %} tag):")
        for rel_path, line_num, url in warnings:
            print(f"  {rel_path}:{line_num}: {url}")
        print()

    if errors:
        print(f"Found {len(errors)} broken URL(s)")
        sys.exit(1)
    elif warnings:
        print(f"Found {len(warnings)} hardcoded URL(s) that could be improved")
        sys.exit(0)
    else:
        print("All template URLs are valid")
        sys.exit(0)


if __name__ == "__main__":
    main()
