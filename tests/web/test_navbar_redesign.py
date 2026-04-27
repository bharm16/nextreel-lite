"""Structural regression tests for the 2026-04-27 navbar redesign.

Asserts copy parity ("Log In"/"Log Out" → "Sign in"/"Sign out"), the new
search trigger element, sentence-case typography on right-side nav links,
and removal of decorative borders from the mobile icon buttons.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAVBAR = ROOT / "templates" / "navbar_modern.html"
INPUT_CSS = ROOT / "static" / "css" / "input.css"


def _navbar() -> str:
    return NAVBAR.read_text(encoding="utf-8")


def _input_css() -> str:
    return INPUT_CSS.read_text(encoding="utf-8")


# ── Copy changes ────────────────────────────────────────────────

def test_navbar_uses_sign_in_copy():
    html = _navbar()
    assert "Sign in" in html, "navbar must use 'Sign in' (replaces 'Log In')"


def test_navbar_does_not_use_log_in_copy():
    html = _navbar()
    # case-sensitive "Log In" / "Log in" should be gone; allow "login" in URLs
    assert "Log In" not in html, "navbar must not contain literal 'Log In' text"
    assert "Log in" not in html, "navbar must not contain literal 'Log in' text"


def test_navbar_uses_sign_out_copy():
    html = _navbar()
    assert "Sign out" in html, "navbar must use 'Sign out' (replaces 'Log out'/'Log Out')"


def test_navbar_does_not_use_log_out_copy():
    html = _navbar()
    assert "Log out" not in html, "navbar must not contain literal 'Log out' text"
    assert "Log Out" not in html, "navbar must not contain literal 'Log Out' text"


# ── Typography for right-side nav links (.navbar-link) ─────────

def _navbar_link_block() -> str:
    """Extract the `.navbar-link {…}` rule body from input.css.

    Anchors on a line that *starts* with `.navbar-link {` so the regex can't
    accidentally match a descendant compound like `.navbar--solid .navbar-link {`
    if a future CSS reorder moves the descendant rule above the base rule.
    """
    css = _input_css()
    match = re.search(
        r"^\s*\.navbar-link\s*\{([^}]*)\}", css, re.DOTALL | re.MULTILINE
    )
    assert match, "could not locate .navbar-link rule in input.css"
    return match.group(1)


def test_navbar_link_uses_sentence_case():
    body = _navbar_link_block()
    # Either no text-transform property, or explicitly set to none.
    assert "text-transform: uppercase" not in body, (
        ".navbar-link must not be uppercase (sentence-case redesign)"
    )


def test_navbar_link_uses_thirteen_px_size():
    body = _navbar_link_block()
    assert "font-size: 13px" in body, ".navbar-link must use 13px font-size"


def test_navbar_link_uses_tight_letter_spacing():
    body = _navbar_link_block()
    assert "letter-spacing: 0.06em" in body, (
        ".navbar-link must use 0.06em letter-spacing (down from 0.14em)"
    )


def test_navbar_link_solid_state_uses_color_text():
    """Solid-state variant must remain visible on the cream bg."""
    css = _input_css()
    # Match the .navbar.navbar--solid .navbar-link rule (color only).
    match = re.search(
        r"\.navbar\.navbar--solid\s+\.navbar-link\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert match, "could not locate .navbar--solid .navbar-link rule"
    assert "var(--color-text)" in match.group(1), (
        "solid-state .navbar-link must use --color-text for cream-bg contrast"
    )


# ── Search trigger element (desktop) ────────────────────────────

def test_desktop_search_trigger_keeps_existing_id():
    """Element id must stay 'searchSpotlightTrigger' so search-spotlight.js
    binds without modification."""
    html = _navbar()
    assert 'id="searchSpotlightTrigger"' in html, (
        "desktop search trigger must keep id='searchSpotlightTrigger' "
        "so the existing JS binding continues to work"
    )


def test_desktop_search_trigger_uses_new_class():
    html = _navbar()
    assert "navbar-search-trigger" in html, (
        "desktop search trigger must carry the .navbar-search-trigger class"
    )


def test_desktop_search_trigger_shows_placeholder_label():
    html = _navbar()
    assert "Search films, actors" in html, (
        "desktop search trigger must show 'Search films, actors…' placeholder text"
    )


def test_desktop_search_trigger_aria_attributes_preserved():
    html = _navbar()
    assert 'aria-label="Open search"' in html, "must keep aria-label='Open search'"
    assert 'aria-haspopup="dialog"' in html, "must keep aria-haspopup='dialog'"
    assert 'aria-controls="searchSpotlight"' in html, "must keep aria-controls='searchSpotlight'"


def test_desktop_search_trigger_is_button_element():
    """A button (not a real <input>) keeps the spotlight modal as the
    single source of input state."""
    html = _navbar()
    pattern = r'<button[^>]*id="searchSpotlightTrigger"'
    assert re.search(pattern, html), (
        "search trigger must be a <button> element, not <input>"
    )


# ── Search trigger CSS ─────────────────────────────────────────

def _search_trigger_block() -> str:
    css = _input_css()
    match = re.search(
        r"^\s*\.navbar-search-trigger\s*\{([^}]*)\}",
        css,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "could not locate .navbar-search-trigger rule in input.css"
    return match.group(1)


def test_search_trigger_has_white_background():
    body = _search_trigger_block()
    assert re.search(r"background:\s*#ffffff", body), (
        ".navbar-search-trigger must have a white (#ffffff) background"
    )


def test_search_trigger_centered_via_auto_margin():
    body = _search_trigger_block()
    assert "margin: 0 auto" in body, (
        ".navbar-search-trigger must be centered via 'margin: 0 auto'"
    )


def test_search_trigger_default_width_380():
    body = _search_trigger_block()
    assert "width: 380px" in body, (
        ".navbar-search-trigger must default to 380px width"
    )


def test_search_trigger_solid_state_has_inset_hairline():
    """Without the hairline, the white field has no separation from the
    cream solid-state navbar bg."""
    css = _input_css()
    pattern = r"\.navbar\.navbar--solid\s+\.navbar-search-trigger\s*\{([^}]*)\}"
    match = re.search(pattern, css, re.DOTALL)
    assert match, "could not locate .navbar--solid .navbar-search-trigger rule"
    assert "inset 0 0 0 1px" in match.group(1), (
        "solid-state search trigger must have an inset 1px hairline for "
        "separation from cream bg"
    )


def test_search_trigger_narrows_when_logged_in():
    """When the navbar carries data-authenticated='true', the field shrinks
    to 340px to make room for Watched/Watchlist + avatar."""
    css = _input_css()
    pattern = (
        r'\.navbar\[data-authenticated="true"\]\s+\.navbar-search-trigger\s*\{([^}]*)\}'
    )
    match = re.search(pattern, css, re.DOTALL)
    assert match, (
        "logged-in width override rule for .navbar-search-trigger missing"
    )
    assert "width: 340px" in match.group(1), (
        "logged-in .navbar-search-trigger must shrink to 340px"
    )


# ── data-authenticated attribute ───────────────────────────────

def test_navbar_root_carries_data_authenticated_attribute():
    """The CSS uses [data-authenticated="true"] to size the search trigger;
    the template must emit the attribute reflecting the auth state."""
    html = _navbar()
    pattern = r'<header[^>]*data-authenticated="\{\{[^}]*current_user_id[^}]*\}\}"'
    assert re.search(pattern, html), (
        "<header class='navbar'> must set data-authenticated based on current_user_id"
    )


# ── Icon button border removal ─────────────────────────────────

def _icon_btn_block() -> str:
    css = _input_css()
    # Match the base .navbar-icon-btn rule anchored at line start, so we never
    # pick up a descendant compound like `.navbar--solid .navbar-icon-btn {`.
    match = re.search(
        r"^\s*\.navbar-icon-btn\s*\{([^}]*)\}",
        css,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "could not locate .navbar-icon-btn rule in input.css"
    return match.group(1)


def test_navbar_icon_btn_has_no_border():
    body = _icon_btn_block()
    has_explicit_zero = re.search(r"border:\s*(0|none)\b", body)
    has_decorative_border = re.search(r"border:\s*1px\s+solid", body)
    assert not has_decorative_border, (
        ".navbar-icon-btn must not carry a 1px decorative border"
    )
    assert has_explicit_zero, (
        ".navbar-icon-btn must explicitly set border: 0 to override UA defaults"
    )


def test_navbar_icon_btn_hover_uses_color_only():
    """Hover compensates for the missing border with a color shift."""
    css = _input_css()
    match = re.search(r"\.navbar-icon-btn:hover\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, "could not locate .navbar-icon-btn:hover rule"
    body = match.group(1)
    assert "border-color" not in body, (
        ".navbar-icon-btn:hover must not animate border-color (no border)"
    )
    assert "color:" in body, (
        ".navbar-icon-btn:hover must shift the icon stroke color"
    )


# ── Mobile menu typography ─────────────────────────────────────

def _mobile_links_block() -> str:
    css = _input_css()
    match = re.search(
        r"^\s*\.navbar-mobile-links\s+a\s*,\s*\.navbar-mobile-links\s+button\s*\{([^}]*)\}",
        css,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "could not locate .navbar-mobile-links a, …button rule"
    return match.group(1)


def test_mobile_links_use_sentence_case():
    body = _mobile_links_block()
    assert "text-transform: uppercase" not in body, (
        "mobile menu links must be sentence-case"
    )


def test_mobile_links_use_fifteen_px_size():
    body = _mobile_links_block()
    assert "font-size: 15px" in body, "mobile menu links must use 15px font-size"


def test_mobile_signout_button_uses_accent_color():
    """The Sign-out button signals destructive action with the accent color."""
    html = _navbar()
    pattern = (
        r'<form\s+method="POST"\s+action="/logout"[^>]*>\s*'
        r'<input[^>]+>\s*'
        r'<button[^>]*class="[^"]*mobile-link-signout[^"]*"'
    )
    assert re.search(pattern, html, re.DOTALL), (
        "mobile menu Sign out button must carry the .mobile-link-signout class"
    )

    css = _input_css()
    match = re.search(r"\.mobile-link-signout\s*\{([^}]*)\}", css, re.DOTALL)
    assert match, "missing .mobile-link-signout CSS rule"
    assert "var(--color-accent)" in match.group(1), (
        ".mobile-link-signout must use --color-accent"
    )
