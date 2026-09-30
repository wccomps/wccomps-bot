"""Tests for NAV_MAPPING consistency with URL configuration."""

from django.urls import URLPattern, URLResolver, get_resolver

from core.context_processors import NAV_MAPPING


def _url_names(patterns: list[URLPattern | URLResolver]) -> set[str]:
    """Every URL name in the project, unqualified, as request.resolver_match.url_name reports it."""
    names: set[str] = set()
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            names |= _url_names(pattern.url_patterns)
        elif pattern.name:
            names.add(pattern.name)
    return names


def test_all_nav_mapping_url_names_exist():
    """Every key in NAV_MAPPING must name a URL pattern, or it is a stale entry."""
    stale = sorted(set(NAV_MAPPING) - _url_names(get_resolver().url_patterns))
    assert not stale, f"NAV_MAPPING contains URL names no pattern has: {stale}"


def test_nav_mapping_values_are_tuples():
    """All NAV_MAPPING values must be (nav, subnav) string tuples."""
    for url_name, value in NAV_MAPPING.items():
        assert isinstance(value, tuple), f"{url_name}: expected tuple, got {type(value)}"
        assert len(value) == 2, f"{url_name}: expected 2-tuple, got {len(value)}-tuple"
        assert isinstance(value[0], str), f"{url_name}: nav must be str"
        assert isinstance(value[1], str), f"{url_name}: subnav must be str"
