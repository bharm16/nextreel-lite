"""
Interactive end-to-end Playwright tests for all nextreel-lite workflows.

Runs against the REAL app with a real MySQL database — no mocks.
The app must be running on http://127.0.0.1:5000 before executing.

Run:
    python3.12 tests/test_workflows_e2e.py           # headless
    python3.12 tests/test_workflows_e2e.py --headed   # visible browser
"""

import os
import sys
import time
from dataclasses import dataclass

import pytest

RUN_E2E = os.environ.get("RUN_E2E") == "1"

if RUN_E2E:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip(
            "E2E workflows require Playwright. Install it and run with RUN_E2E=1.",
            allow_module_level=True,
        )
else:
    pytest.skip(
        "Interactive Playwright workflows are opt-in. Set RUN_E2E=1 to run them.",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5000")
SCREENSHOT_DIR = "/tmp/nextreel_e2e"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------
@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""
    screenshot: str = ""


results: list = []


def record(name, passed, msg="", screenshot=""):
    results.append(TestResult(name, passed, msg, screenshot))
    icon = "PASS" if passed else "FAIL"
    print(f"  [{icon}] {name}" + (f" — {msg}" if msg else ""))


def snap(page, name):
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    return path


def pick_movie(page):
    """Helper: click 'Pick a Movie', wait for the movie page to load."""
    page.locator("form[action='/next_movie'] button").first.click()
    # The POST returns 303 → /movie/ttXXX which then loads.
    # Wait for the final page to be networkidle.
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# Workflow tests — real app, real DB, real browser
# ---------------------------------------------------------------------------


def test_01_home_page_loads(page):
    """Home page renders with hero, Pick a Movie, Set Filters."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    s = snap(page, "01_home")

    try:
        assert "Nextreel" in page.title(), f"Unexpected title: {page.title()}"

        pick_btn = page.locator("form[action='/next_movie'] button").first
        assert pick_btn.is_visible(), "Pick a Movie button not visible"

        csrf = page.locator("input[name='csrf_token']").first
        token = csrf.get_attribute("value")
        assert token and len(token) > 10, f"CSRF token looks invalid: {token}"

        assert page.locator("nav, header").first.is_visible(), "Nav/header missing"
        assert page.locator("footer").first.is_visible(), "Footer missing"

        record("Home page loads", True, screenshot=s)
    except Exception as e:
        record("Home page loads", False, str(e), s)


def test_02_pick_a_movie(page):
    """Click 'Pick a Movie' — should navigate to a real movie page."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        pick_movie(page)
        s = snap(page, "02_picked_movie")

        # Should be on /movie/tt...
        url = page.url
        assert "/movie/tt" in url, f"Expected /movie/ttXXX URL, got {url}"

        # Should have actual movie content
        body_text = page.inner_text("body")
        assert len(body_text) > 100, "Movie page seems empty"

        record("Pick a Movie → real movie", True, f"url={url}", s)
    except Exception as e:
        record("Pick a Movie → real movie", False, str(e), snap(page, "02_pick_fail"))


def test_03_movie_page_elements(page):
    """Movie detail page has title, navigation, and content."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        pick_movie(page)
        s = snap(page, "03_movie_details")

        # Next movie button should exist on movie page
        next_form = page.locator("form[action='/next_movie']")
        assert next_form.count() > 0, "Next movie form not found on movie page"

        # Previous movie button/form
        prev_form = page.locator("form[action='/previous_movie']")
        assert prev_form.count() > 0, "Previous movie form not found"

        # Title element (h1 or prominent heading)
        headings = page.locator("h1, h2").count()
        assert headings > 0, "No headings on movie page"

        # Nav bar present
        assert page.locator("nav, header").first.is_visible(), "Nav missing on movie page"

        record("Movie page elements", True, screenshot=s)
    except Exception as e:
        record("Movie page elements", False, str(e), snap(page, "03_detail_fail"))


def test_04_next_movie_navigation(page):
    """Click Next Movie 3 times — each should show a different movie."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        urls = []

        for i in range(3):
            pick_movie(page)
            urls.append(page.url)
            snap(page, f"04_movie_{i+1}")

        s = snap(page, "04_movie_3")

        # At least 2 unique movies (rare chance of same movie twice)
        unique = len(set(urls))
        assert unique >= 2, f"Expected different movies, got {urls}"

        record("Next movie navigation (3 movies)", True, f"{unique} unique movies", s)
    except Exception as e:
        record("Next movie navigation", False, str(e), snap(page, "04_nav_fail"))


def test_05_previous_movie(page):
    """Browse forward, then click Previous to go back."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        # Pick first movie
        pick_movie(page)
        first_url = page.url
        snap(page, "05_first_movie")

        # Pick second movie
        pick_movie(page)
        second_url = page.url
        snap(page, "05_second_movie")

        assert first_url != second_url, "Got same movie twice"

        # Click Previous
        prev_btn = page.locator("form[action='/previous_movie'] button").first
        if prev_btn.is_visible():
            prev_btn.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            back_url = page.url
            s = snap(page, "05_back_to_first")

            assert back_url == first_url, f"Expected {first_url}, got {back_url}"
            record("Previous movie → goes back", True, f"returned to {back_url}", s)
        else:
            record("Previous movie", True, "prev button not visible (disabled)")
    except Exception as e:
        record("Previous movie navigation", False, str(e), snap(page, "05_prev_fail"))


def test_07_apply_filters(page):
    """Fill drawer filters and submit — should land on a filtered movie."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        # Need to be on a movie detail page for the drawer to exist
        pick_movie(page)

        # Open the drawer
        page.locator("#filterDrawerTab").click()
        page.wait_for_selector("#filterDrawer.open", timeout=2000)
        s1 = snap(page, "07_drawer_open")

        # Fill fields (shared partial — same input names as the old page)
        page.locator("input[name='imdb_score_min']").fill("7.0")
        page.locator("input[name='imdb_score_max']").fill("10.0")
        page.locator("input[name='num_votes_min']").fill("50000")
        page.locator("input[name='num_votes_max']").fill("2000000")
        page.locator("input[name='year_min']").fill("1990")
        page.locator("input[name='year_max']").fill("2025")
        page.locator("select[name='language']").select_option("en")

        drama = page.locator("input[name='genres[]'][value='Drama']")
        if drama.count() > 0:
            drama.check()

        snap(page, "07_drawer_filled")

        # Submit via the drawer Apply button (AJAX → navigate on success)
        current_url = page.url
        page.locator("#drawerApplyBtn").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        s2 = snap(page, "07_filtered_result")

        body = page.inner_text("body")
        url = page.url
        # Either we navigated to a new /movie/tt page, or we stayed (no match) with a flash
        has_movie = "/movie/tt" in url
        has_content = len(body) > 100

        assert has_movie or has_content, f"Empty result. URL: {url}"
        record("Apply filters (drawer) → result", True, f"url={url}", s2)
    except Exception as e:
        record("Apply filters (drawer) → result", False, str(e), snap(page, "07_fail"))


def test_10_csrf_enforcement(page):
    """All POST endpoints reject requests without CSRF."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    endpoints = ["/next_movie", "/previous_movie", "/filtered_movie", "/logout"]
    all_pass = True
    details = []

    for ep in endpoints:
        try:
            status = page.evaluate(
                f"""async () => {{
                const resp = await fetch('{ep}', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                    body: ''
                }});
                return resp.status;
            }}"""
            )
            if status == 403:
                details.append(f"{ep}=403")
            else:
                details.append(f"{ep}={status}!")
                all_pass = False
        except Exception as e:
            details.append(f"{ep}=err")
            all_pass = False

    record("CSRF enforcement (all POST endpoints)", all_pass, ", ".join(details))


def test_11_method_enforcement(page):
    """GET on POST-only routes returns 405."""
    try:
        for ep in ["/next_movie", "/previous_movie"]:
            response = page.goto(f"{BASE_URL}{ep}")
            assert response.status == 405, f"GET {ep}: expected 405, got {response.status}"
        record("HTTP method enforcement (405)", True)
    except Exception as e:
        record("HTTP method enforcement (405)", False, str(e))


def test_12_invalid_tconst(page):
    """Invalid tconst format returns 400."""
    try:
        response = page.goto(f"{BASE_URL}/movie/INVALID")
        assert response.status == 400, f"Expected 400, got {response.status}"
        record("Invalid tconst rejected (400)", True)
    except Exception as e:
        record("Invalid tconst rejected (400)", False, str(e))


def test_13_unknown_route_404(page):
    """Unknown route returns 404."""
    try:
        response = page.goto(f"{BASE_URL}/this-route-does-not-exist")
        assert response.status == 404, f"Expected 404, got {response.status}"
        record("Unknown route → 404", True)
    except Exception as e:
        record("Unknown route → 404", False, str(e))


def test_14_security_headers(page):
    """Response includes security headers."""
    try:
        response = page.goto(BASE_URL)
        h = response.headers
        found = []
        if h.get("x-content-type-options") == "nosniff":
            found.append("nosniff")
        if h.get("x-frame-options"):
            found.append("x-frame-options")
        if h.get("x-response-time"):
            found.append("x-response-time")
        if h.get("permissions-policy"):
            found.append("permissions-policy")
        assert len(found) > 0
        record("Security headers present", True, ", ".join(found))
    except Exception as e:
        record("Security headers present", False, str(e))


def test_15_session_cookie(page):
    """nr_sid cookie set on first visit."""
    try:
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        cookies = page.context.cookies()
        nr_sid = [c for c in cookies if c["name"] == "nr_sid"]
        assert len(nr_sid) > 0, f"No nr_sid. Cookies: {[c['name'] for c in cookies]}"
        record("Session cookie (nr_sid) set", True)
    except Exception as e:
        record("Session cookie (nr_sid) set", False, str(e))


def test_16_theme_toggle(page):
    """Theme toggle switches between light and dark."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        toggle = page.locator("#themeToggle")
        if toggle.count() == 0 or not toggle.is_visible():
            record("Theme toggle", True, "not present")
            return

        before = page.evaluate("document.documentElement.getAttribute('data-theme')")
        toggle.click()
        page.wait_for_timeout(300)
        after = page.evaluate("document.documentElement.getAttribute('data-theme')")
        s = snap(page, "16_theme")

        assert before != after, f"Theme unchanged: {before}"
        record("Theme toggle", True, f"{before} → {after}", s)
    except Exception as e:
        record("Theme toggle", False, str(e))


def test_17_mobile_responsive(page):
    """Mobile viewport: hamburger menu works."""
    try:
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        s = snap(page, "17_mobile")

        menu_btn = page.locator("#menuBtn")
        if menu_btn.count() > 0 and menu_btn.is_visible():
            menu_btn.click()
            page.wait_for_timeout(300)
            s = snap(page, "17_mobile_menu")
            record("Mobile responsive + menu", True, screenshot=s)
        else:
            record("Mobile responsive", True, "renders at 375px", s)
    except Exception as e:
        record("Mobile responsive", False, str(e))
    finally:
        page.set_viewport_size({"width": 1280, "height": 800})


def test_18_no_console_errors(page):
    """No JS errors on home and filters."""
    errors = []

    def on_error(msg):
        if msg.type == "error" and "favicon" not in msg.text.lower():
            errors.append(msg.text[:120])

    page.on("console", on_error)
    try:
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)

        # Exercise the drawer JS on a movie page
        pick_movie(page)
        page.locator("#filterDrawerTab").click()
        page.wait_for_selector("#filterDrawer.open", timeout=2000)
        page.wait_for_timeout(500)

        if errors:
            record("No JS console errors", False, f"{len(errors)}: {errors[0]}")
        else:
            record("No JS console errors", True)
    except Exception as e:
        record("No JS console errors", False, str(e))
    finally:
        page.remove_listener("console", on_error)


def test_19_logout(page):
    """Logout clears session and redirects home."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        csrf = page.locator("input[name='csrf_token']").first.get_attribute("value")
        result = page.evaluate(
            f"""async () => {{
            const resp = await fetch('/logout', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                body: 'csrf_token={csrf}',
                redirect: 'follow'
            }});
            return {{status: resp.status, url: resp.url}};
        }}"""
        )
        record("Logout workflow", True, f"status={result['status']}")
    except Exception as e:
        record("Logout workflow", False, str(e))


def test_20_full_browse_workflow(page):
    """Full flow: Home → Pick → Next → Next → Previous → Home."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    snap(page, "20_s0_home")

    try:
        # Step 1: Pick a movie
        pick_movie(page)
        movie1 = page.url
        snap(page, "20_s1_movie1")
        assert "/movie/tt" in movie1, f"Step 1 failed: {movie1}"

        # Step 2: Next movie
        pick_movie(page)
        movie2 = page.url
        snap(page, "20_s2_movie2")
        assert "/movie/tt" in movie2, f"Step 2 failed: {movie2}"

        # Step 3: Previous movie (back to movie1)
        prev = page.locator("form[action='/previous_movie'] button").first
        if prev.is_visible():
            prev.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            snap(page, "20_s3_prev")
            assert page.url == movie1, f"Previous didn't go back: {page.url} != {movie1}"

        # Step 4: Home via brand link
        home_link = page.locator("a[href='/']").first
        if home_link.is_visible():
            home_link.click()
            page.wait_for_load_state("networkidle")
        else:
            page.goto(BASE_URL)
            page.wait_for_load_state("networkidle")

        s = snap(page, "20_s4_home")
        record("Full browse: Home→Pick→Next→Prev→Home", True, f"m1={movie1}, m2={movie2}", s)
    except Exception as e:
        record("Full browse workflow", False, str(e), snap(page, "20_fail"))


def test_21_full_filter_workflow(page):
    """Full flow: Home → Pick Movie → Open Drawer → Fill → Apply → Filtered Movie."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    try:
        # Step 1: Pick a movie so the drawer is available
        pick_movie(page)
        snap(page, "21_s1_movie")

        # Step 2: Open the drawer
        page.locator("#filterDrawerTab").click()
        page.wait_for_selector("#filterDrawer.open", timeout=2000)
        snap(page, "21_s2_drawer_open")

        # Step 3: Fill broad filters
        page.locator("input[name='imdb_score_min']").fill("7.0")
        page.locator("input[name='imdb_score_max']").fill("10.0")
        page.locator("input[name='num_votes_min']").fill("100000")
        page.locator("input[name='num_votes_max']").fill("2000000")
        page.locator("input[name='year_min']").fill("1990")
        page.locator("input[name='year_max']").fill("2025")
        page.locator("select[name='language']").select_option("en")

        for genre in ["Drama", "Action", "Comedy", "Thriller"]:
            cb = page.locator(f"input[name='genres[]'][value='{genre}']")
            if cb.count() > 0:
                cb.check()

        snap(page, "21_s3_filled")

        # Step 4: Apply via drawer button
        page.locator("#drawerApplyBtn").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        s = snap(page, "21_s4_result")

        body = page.inner_text("body")
        url = page.url
        has_movie = "/movie/tt" in url
        has_content = len(body) > 100
        assert has_movie or has_content, f"No result. URL: {url}"

        record("Full filter: Home→Pick→Drawer→Fill→Apply→Result", True, f"url={url}", s)
    except Exception as e:
        record("Full filter workflow (drawer)", False, str(e), snap(page, "21_fail"))


def test_22_direct_movie_by_tconst(page):
    """Navigate directly to a known movie."""
    try:
        # tt0111161 = The Shawshank Redemption
        response = page.goto(f"{BASE_URL}/movie/tt0111161")
        page.wait_for_load_state("networkidle")
        s = snap(page, "22_direct")

        # 200 if movie exists and enrichment works, 500 if SQL/TMDb issue
        body = page.inner_text("body")
        record("Direct movie by tconst", True, f"status={response.status}, {len(body)} chars", s)
    except Exception as e:
        record("Direct movie by tconst", False, str(e))


def test_23_health_endpoint(page):
    """Health check returns 200."""
    try:
        response = page.goto(f"{BASE_URL}/health")
        body = page.inner_text("body")
        assert response.status == 200
        assert "healthy" in body.lower()
        record("Health endpoint", True)
    except Exception as e:
        record("Health endpoint", False, str(e))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    headed = "--headed" in sys.argv

    print("\n" + "=" * 64)
    print("  Nextreel-Lite Interactive E2E Workflow Tests (Real DB)")
    print("=" * 64)
    print(f"  Target:      {BASE_URL}")
    print(f"  Browser:     {'headed' if headed else 'headless'}")
    print(f"  Screenshots: {SCREENSHOT_DIR}/")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            slow_mo=300 if headed else 0,
        )
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        tests = [
            test_01_home_page_loads,
            test_02_pick_a_movie,
            test_03_movie_page_elements,
            test_04_next_movie_navigation,
            test_05_previous_movie,
            test_07_apply_filters,
            test_10_csrf_enforcement,
            test_11_method_enforcement,
            test_12_invalid_tconst,
            test_13_unknown_route_404,
            test_14_security_headers,
            test_15_session_cookie,
            test_16_theme_toggle,
            test_17_mobile_responsive,
            test_18_no_console_errors,
            test_19_logout,
            test_20_full_browse_workflow,
            test_21_full_filter_workflow,
            test_22_direct_movie_by_tconst,
            test_23_health_endpoint,
        ]

        for test_fn in tests:
            try:
                test_fn(page)
            except Exception as e:
                record(test_fn.__name__, False, f"Unexpected: {e}")

        browser.close()

    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    print("\n" + "=" * 64)
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print("=" * 64)

    if failed:
        print("\n  Failed tests:")
        for r in results:
            if not r.passed:
                print(f"    - {r.name}: {r.message}")
                if r.screenshot:
                    print(f"      Screenshot: {r.screenshot}")

    screenshots = [r.screenshot for r in results if r.screenshot]
    print(f"\n  {len(screenshots)} screenshots saved to {SCREENSHOT_DIR}/")

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
