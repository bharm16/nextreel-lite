"""Unit tests for the _safe_next_path open-redirect guard."""

from __future__ import annotations

import pytest

from nextreel.web.routes.auth import _safe_next_path


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "//evil.com",
        "//evil.com/path",
        "/\\evil.com",
        r"/\evil.com",
        "javascript:alert(1)",
        "http://evil.com",
        "https://evil.com/foo",
        "evil.com",
        "foo/bar",
        "\t//evil.com",
        " /movie/tt0111161",
        "/movie/tt0111161\r\nLocation: evil.com",
        "/foo\nbar",
        "/path\x00with-null",
        "/path\x7fwith-del",
    ],
)
def test_rejects_unsafe(value):
    assert _safe_next_path(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "/",
        "/movie/tt0111161",
        "/watched",
        "/movie/tt0111161?ref=card",
        "/movie/tt0111161#cast",
        "/account?tab=profile",
    ],
)
def test_accepts_safe(value):
    assert _safe_next_path(value) == value
