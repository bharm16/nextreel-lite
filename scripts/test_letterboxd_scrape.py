#!/usr/bin/env python3
"""
Prototype: scrape a Letterboxd user's watched films and match to tconsts.

Usage:
    python3 scripts/test_letterboxd_scrape.py billbadminton
    python3 scripts/test_letterboxd_scrape.py <username> --max-pages 5

Scrapes https://letterboxd.com/{username}/films/ using Playwright,
then matches each film against movie_candidates by (title, year).
"""
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _letterboxd_matcher import load_env, match_films, print_results


async def scrape_letterboxd_films(username: str, max_pages: int = 0) -> list[dict]:
    """Scrape all watched films from a Letterboxd profile using Playwright."""
    from playwright.async_api import async_playwright

    base_url = f"https://letterboxd.com/{username}/films/"
    all_films = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # Load first page and detect total pages
        await page.goto(base_url, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(".film-poster", timeout=15000)
        except Exception:
            print(f"Could not load films page for '{username}'. Check the username.")
            await browser.close()
            return []

        total_pages = await _get_page_count(page)
        if max_pages > 0:
            total_pages = min(total_pages, max_pages)

        print(f"Scraping {total_pages} page(s) for {username}...")

        # Scrape each page
        for pg in range(1, total_pages + 1):
            if pg > 1:
                await page.goto(f"{base_url}page/{pg}/", wait_until="domcontentloaded")
                try:
                    await page.wait_for_selector(".film-poster", timeout=15000)
                except Exception:
                    print(f"  Page {pg}: failed to load, stopping.")
                    break

            page_films = await _extract_films(page)
            all_films.extend(page_films)
            print(f"  Page {pg}/{total_pages}: {len(page_films)} films")

        await browser.close()

    return all_films


async def _get_page_count(page) -> int:
    """Detect total number of film pages."""
    links = await page.query_selector_all(".paginate-page a")
    pages = []
    for link in links:
        text = (await link.inner_text()).strip()
        if text.isdigit():
            pages.append(int(text))
    return max(pages) if pages else 1


async def _extract_films(page) -> list[dict]:
    """Extract film data from the current page's poster grid."""
    posters = await page.query_selector_all(".film-poster")
    films = []
    for poster in posters:
        img = await poster.query_selector("img")
        if not img:
            continue
        alt = await img.get_attribute("alt") or ""
        # Alt text is "Poster for Title (Year)" — strip prefix
        title_with_year = alt.replace("Poster for ", "")
        # Parse "Title (YYYY)" — year is always in trailing parens
        match = re.match(r"^(.+?)\s*\((\d{4})\)\s*$", title_with_year)
        if match:
            films.append({"name": match.group(1), "year": int(match.group(2))})
        elif title_with_year:
            # No year in parens — try without year
            films.append({"name": title_with_year, "year": 0})
    return films


async def main():
    username = sys.argv[1] if len(sys.argv) > 1 else "billbadminton"

    max_pages = 0
    if "--max-pages" in sys.argv:
        idx = sys.argv.index("--max-pages")
        if idx + 1 < len(sys.argv):
            max_pages = int(sys.argv[idx + 1])

    print(f"Letterboxd scraper for: {username}")
    print(f"URL: https://letterboxd.com/{username}/films/\n")

    start = time.monotonic()
    films = await scrape_letterboxd_films(username, max_pages)
    scrape_time = time.monotonic() - start

    if not films:
        print("No films scraped.")
        return

    print(f"\nScraped {len(films)} films in {scrape_time:.1f}s")
    print(f"Matching against database...\n")

    load_env()
    result = await match_films(films)
    print_results(result)


if __name__ == "__main__":
    asyncio.run(main())
