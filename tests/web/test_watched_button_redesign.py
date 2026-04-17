"""Structural regression tests for the 2026-04-16 watched button redesign.

Asserts that the redesigned control lives in the sticky nav bar, uses the new
--color-watched token, carries the .nav-btn-watched class, and that the JS no
longer hardcodes Tailwind utility strings.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tokens_css() -> str:
    return (ROOT / "static" / "css" / "tokens.css").read_text(encoding="utf-8")


def test_color_watched_token_defined_in_light_root():
    css = _tokens_css()
    root_block = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    assert root_block, "could not locate :root block in tokens.css"
    assert "--color-watched:" in root_block.group(1), (
        "--color-watched token must be defined in the :root (light) block"
    )


def test_color_watched_token_defined_in_dark_media_query():
    css = _tokens_css()
    dark_media = re.search(
        r"@media \(prefers-color-scheme: dark\)\s*\{[^{}]*:root\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert dark_media, "could not locate dark prefers-color-scheme :root block"
    assert "--color-watched:" in dark_media.group(1), (
        "--color-watched token must be defined in the dark prefers-color-scheme block"
    )


def test_color_watched_token_defined_in_explicit_light_theme():
    css = _tokens_css()
    light_theme = re.search(r'\[data-theme="light"\]\s*\{([^}]*)\}', css, re.DOTALL)
    assert light_theme, 'could not locate [data-theme="light"] block'
    assert "--color-watched:" in light_theme.group(1), (
        '--color-watched token must be defined in the [data-theme="light"] block'
    )


def test_color_watched_token_defined_in_explicit_dark_theme():
    css = _tokens_css()
    dark_theme = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', css, re.DOTALL)
    assert dark_theme, 'could not locate [data-theme="dark"] block'
    assert "--color-watched:" in dark_theme.group(1), (
        '--color-watched token must be defined in the [data-theme="dark"] block'
    )


def _input_css() -> str:
    return (ROOT / "static" / "css" / "input.css").read_text(encoding="utf-8")


def test_nav_btn_watched_base_rule_exists():
    css = _input_css()
    assert ".nav-btn-watched" in css, (
        ".nav-btn-watched class must be defined in input.css"
    )


def test_nav_btn_watched_uses_color_watched_token_for_watched_state():
    css = _input_css()
    pattern = re.compile(
        r'form\[data-watched-state="watched"\]\s+\.nav-btn-watched\s*\{[^}]*color:\s*var\(--color-watched\)',
        re.DOTALL,
    )
    assert pattern.search(css), (
        "watched-state rule must set color to var(--color-watched)"
    )


def test_nav_btn_watched_unwatched_state_uses_muted_text_token():
    css = _input_css()
    pattern = re.compile(
        r'form\[data-watched-state="unwatched"\]\s+\.nav-btn-watched\s*\{[^}]*color:\s*var\(--color-text-muted\)',
        re.DOTALL,
    )
    assert pattern.search(css), (
        "unwatched-state rule must set color to var(--color-text-muted)"
    )


def _media_blocks(css: str, query: str) -> list[str]:
    """Return contents of every @media block whose query header matches."""
    blocks: list[str] = []
    for m in re.finditer(re.escape(query) + r"\s*\{", css):
        start = m.end()
        depth = 1
        i = start
        while i < len(css) and depth > 0:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        blocks.append(css[start : i - 1])
    return blocks


def test_nav_btn_watched_prefix_hidden_on_mobile():
    css = _input_css()
    blocks = _media_blocks(css, "@media (max-width: 640px)")
    assert blocks, "could not locate any @media (max-width: 640px) block"
    joined = "\n".join(blocks)
    assert ".nav-btn-watched__prefix" in joined, (
        ".nav-btn-watched__prefix must be defined inside a 640px media query"
    )
    prefix_idx = joined.index(".nav-btn-watched__prefix")
    nearby = joined[prefix_idx : prefix_idx + 100]
    assert "display: none" in nearby, (
        ".nav-btn-watched__prefix rule must use display: none"
    )


def _movie_card_template() -> str:
    return (ROOT / "templates" / "movie_card.html").read_text(encoding="utf-8")


def test_standalone_watched_block_removed():
    html = _movie_card_template()
    pattern = re.compile(
        r'<div[^>]*class="[^"]*\bmt-3\b[^"]*"[^>]*>\s*\{%\s*if is_watched',
        re.DOTALL,
    )
    assert not pattern.search(html), (
        "standalone <div class='mt-3'> watched block must be removed"
    )


def test_watched_form_lives_inside_movie_nav_bar():
    html = _movie_card_template()
    nav_block = re.search(
        r'<nav[^>]*class="movie-nav-bar"[^>]*>(.*?)</nav>',
        html,
        re.DOTALL,
    )
    assert nav_block, "could not locate <nav class='movie-nav-bar'> block"
    assert "data-watched-toggle-form" in nav_block.group(1), (
        "data-watched-toggle-form must live inside .movie-nav-bar"
    )


def test_watched_form_gated_by_current_user_id():
    html = _movie_card_template()
    nav_block = re.search(
        r'<nav[^>]*class="movie-nav-bar"[^>]*>(.*?)</nav>',
        html,
        re.DOTALL,
    )
    assert nav_block, "could not locate <nav class='movie-nav-bar'> block"
    inner = nav_block.group(1)
    assert "{% if current_user_id %}" in inner, (
        "watched form must be gated by {% if current_user_id %}"
    )
    guard_pos = inner.index("{% if current_user_id %}")
    form_pos = inner.index("data-watched-toggle-form")
    assert guard_pos < form_pos, (
        "{% if current_user_id %} must appear before the watched form"
    )


def test_watched_button_uses_nav_btn_watched_class():
    html = _movie_card_template()
    pattern = re.compile(
        r'data-watched-toggle-form[^>]*>.*?class="[^"]*\bnav-btn-watched\b[^"]*"',
        re.DOTALL,
    )
    assert pattern.search(html), (
        "watched toggle button must have the nav-btn-watched class"
    )


def test_watched_button_prefix_span_present():
    html = _movie_card_template()
    assert 'class="nav-btn-watched__prefix"' in html, (
        "label must include <span class='nav-btn-watched__prefix'>Mark as </span> for mobile truncation"
    )


def test_watched_form_retains_data_attributes_for_js():
    """JS selector hasn't changed — form must still expose the attributes the IIFE reads."""
    html = _movie_card_template()
    for attr in (
        "data-watched-toggle-form",
        "data-watched-state",
        "data-add-url",
        "data-remove-url",
    ):
        assert attr in html, f"form must retain {attr} for movie-card.js to hook in"
    assert "data-watched-toggle-button" in html


def _movie_card_js() -> str:
    return (ROOT / "static" / "js" / "movie-card.js").read_text(encoding="utf-8")


def test_movie_card_js_no_longer_hardcodes_tailwind_watched_classes():
    js = _movie_card_js()
    for forbidden in ("bg-green-600", "bg-green-700", "rounded-full"):
        assert forbidden not in js, (
            f"{forbidden!r} must not appear in movie-card.js — styling belongs in CSS"
        )


def test_movie_card_js_does_not_mutate_button_classname():
    js = _movie_card_js()
    assert "button.className" not in js, (
        "movie-card.js must not mutate button.className — "
        "styling is driven by data-watched-state on the form"
    )


def test_movie_card_js_still_toggles_data_watched_state():
    js = _movie_card_js()
    assert "dataset.watchedState" in js or 'dataset["watchedState"]' in js, (
        "movie-card.js must still toggle form.dataset.watchedState"
    )


def test_movie_card_js_still_updates_aria_pressed():
    js = _movie_card_js()
    assert 'setAttribute("aria-pressed"' in js, (
        "movie-card.js must continue to manage aria-pressed for a11y"
    )
