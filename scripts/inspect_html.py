"""Inspect saved HTML from debug_scan to understand FB DOM shape."""
import sys
from pathlib import Path

from bs4 import BeautifulSoup


def inspect(path: str) -> None:
    html = Path(path).read_text()
    soup = BeautifulSoup(html, "html.parser")

    arts = soup.select('[role="article"]')
    print(f"ROLE_ARTICLE_COUNT: {len(arts)}")
    for i, a in enumerate(arts[:3]):
        aria = a.get("aria-label")
        print(f"\n--- ARTICLE {i} aria-label={aria} ---")
        links = [x.get("href", "")[:90] for x in a.select("a[href]")[:6]]
        print(f"links: {links}")
        abbrs = [x.get_text(strip=True) for x in a.select("abbr")[:3]]
        print(f"abbrs: {abbrs}")
        times = [x.get("data-utime") or x.get("datetime") for x in a.select("abbr, time")[:3]]
        print(f"timestamps: {times}")
        txt = a.get_text(separator=" ", strip=True)[:250]
        print(f"text: {txt}")

    print("\n" + "=" * 60)
    posis = soup.select("div[aria-posinset]")
    print(f"POSINSET_COUNT: {len(posis)}")
    for i, p in enumerate(posis[:4]):
        aria = p.get("aria-posinset")
        print(f"\n=== POSINSET {i} posinset={aria} ===")
        links = [x.get("href", "")[:90] for x in p.select("a[href]")[:6]]
        print(f"links: {links}")
        abbrs = [x.get_text(strip=True) for x in p.select("abbr")[:3]]
        print(f"abbrs: {abbrs}")
        txt = p.get_text(separator=" ", strip=True)[:300]
        print(f"text: {txt}")


if __name__ == "__main__":
    inspect(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fb_debug_m.html")
