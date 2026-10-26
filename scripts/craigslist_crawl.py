#!/usr/bin/env python3
"""Flushing (Queens) craigslist crawler.

Pulls for-sale listings from the craigslist Queens subarea (`que`, which covers
Flushing) across a set of search terms and writes a deduped snapshot to JSON +
markdown. Uses the server-rendered static search page, so no JS/API decoding is
needed.

Usage:
    python scripts/craigslist_crawl.py
    python scripts/craigslist_crawl.py --queries hardware tools --out /tmp/cl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

BASE = "https://newyork.craigslist.org/search/que/sss"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# "hardware and other etc" — for-sale terms relevant to the Flushing hunt.
DEFAULT_QUERIES = [
    "hardware",
    "tools",
    "electronics",
    "computer",
    "appliances",
]


def fetch(query: str) -> str:
    url = f"{BASE}?query={quote_plus(query)}"
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    resp.raise_for_status()
    return resp.text


def parse(html: str, query: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.cl-static-search-result"):
        a = li.find("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        pid = href.rstrip("/").split("/")[-1].removesuffix(".html")
        # URL shape: .../que/<cat>/d/<slug>/<pid>.html
        parts = href.split("/")
        cat = parts[4] if len(parts) > 4 else ""
        title = (li.get("title") or "").strip()
        price_el = li.select_one(".price")
        loc_el = li.select_one(".location")
        out.append(
            {
                "pid": pid,
                "title": title,
                "price": price_el.get_text(strip=True) if price_el else "",
                "location": loc_el.get_text(strip=True) if loc_el else "",
                "category": cat,
                "url": href,
                "matched_query": query,
            }
        )
    return out


def crawl(queries: list[str], delay: float = 1.0) -> list[dict]:
    by_pid: dict[str, dict] = {}
    for q in queries:
        try:
            rows = parse(fetch(q), q)
        except requests.RequestException as e:
            print(f"  ! {q}: {e}", file=sys.stderr)
            continue
        new = 0
        for r in rows:
            if r["pid"] not in by_pid:
                by_pid[r["pid"]] = r
                new += 1
        print(f"  {q}: {len(rows)} results ({new} new)")
        time.sleep(delay)
    return sorted(by_pid.values(), key=lambda r: (r["category"], r["title"].lower()))


def write_markdown(listings: list[dict], path: Path) -> None:
    lines = [
        "# Flushing (Queens) Craigslist — for-sale snapshot",
        "",
        f"**{len(listings)} unique listings** across queries: "
        + ", ".join(f"`{q}`" for q in DEFAULT_QUERIES),
        "",
        "| Price | Title | Cat | Location | Link |",
        "|------:|-------|-----|----------|------|",
    ]
    for r in listings:
        title = r["title"].replace("|", "\\|")
        lines.append(
            f"| {r['price'] or '—'} | {title} | {r['category']} | "
            f"{r['location'] or '—'} | [link]({r['url']}) |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queries", nargs="+", default=DEFAULT_QUERIES)
    ap.add_argument(
        "--out",
        default=str(Path(__file__).parent / "craigslist_flushing"),
        help="output path prefix (writes <prefix>_listings.json and .md)",
    )
    args = ap.parse_args()

    print(f"Crawling Queens craigslist for {len(args.queries)} queries...")
    listings = crawl(args.queries)

    out = Path(args.out)
    json_path = out.with_name(out.name + "_listings.json")
    md_path = out.with_name(out.name + "_listings.md")
    json_path.write_text(json.dumps(listings, indent=2), encoding="utf-8")
    write_markdown(listings, md_path)

    print(f"\nWrote {len(listings)} listings:")
    print(f"  {json_path}")
    print(f"  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
