#!/usr/bin/env python3
"""
Prototype: import Letterboxd watched films from CSV export and match to tconsts.

Usage:
    python3 scripts/test_letterboxd_csv.py ~/Downloads/letterboxd-*/watched.csv

Reads the Letterboxd watched.csv (columns: Date, Name, Year, Letterboxd URI)
and matches each film against the movie_candidates table by (primaryTitle, startYear).
"""
import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _letterboxd_matcher import load_env, match_films, print_results


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/test_letterboxd_csv.py <path-to-watched.csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1]).expanduser()
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    # Parse CSV
    films = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "").strip()
            year_raw = row.get("Year", "").strip()
            if name and year_raw:
                try:
                    year = int(year_raw)
                except ValueError:
                    continue
                films.append({"name": name, "year": year})

    print(f"Parsed {len(films)} films from {csv_path.name}\n")

    if not films:
        print("No films found in CSV.")
        return

    load_env()
    result = await match_films(films)
    print_results(result)


if __name__ == "__main__":
    asyncio.run(main())
