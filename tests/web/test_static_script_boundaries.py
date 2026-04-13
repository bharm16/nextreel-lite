import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INLINE_SCRIPT_RE = re.compile(
    r"<script\b(?![^>]*\bsrc=)[^>]*>.*?</script>",
    re.IGNORECASE | re.DOTALL,
)


def _template(name: str) -> str:
    return (ROOT / "templates" / name).read_text(encoding="utf-8")


def _asset(path: str) -> Path:
    return ROOT / "static" / "js" / path


def test_watched_list_template_delegates_browser_behavior_to_static_assets():
    html = _template("watched_list.html")

    assert "js/theme-boot.js" in html
    assert "js/watched-list.js" in html
    assert "js/watched-enrichment-progress.js" in html
    assert INLINE_SCRIPT_RE.findall(html) == []

    assert _asset("theme-boot.js").exists()
    assert _asset("watched-list.js").exists()
    assert _asset("watched-enrichment-progress.js").exists()


def test_movie_card_template_delegates_browser_behavior_to_static_assets():
    html = _template("movie_card.html")

    assert "js/movie-card.js" in html
    assert INLINE_SCRIPT_RE.findall(html) == []

    assert _asset("movie-card.js").exists()
