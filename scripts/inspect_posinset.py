"""Inspect aria-posinset structure from saved FB HTML dumps."""
import sys
from pathlib import Path
from bs4 import BeautifulSoup


def inspect(path: str) -> None:
    html = Path(path).read_text()
    soup = BeautifulSoup(html, "html.parser")

    posis = soup.select("div[aria-posinset]")
    print(f"POSINSET_COUNT: {len(posis)}")

    for i, p in enumerate(posis[:5]):
        pos = p.get("aria-posinset")
        print(f"\n=== POSINSET {i} posinset={pos} html_len={len(str(p))} ===")
        links = [x.get("href", "")[:100] for x in p.select("a[href]")]
        print(f"links ({len(links)}):")
        for L in links[:12]:
            print(f"  {L}")
        # aria-label collected elements (reactions, comments, shares)
        aria_labels = [x.get("aria-label") for x in p.select("[aria-label]")
                       if x.get("aria-label") and len(x.get("aria-label")) < 120]
        print(f"aria_labels ({len(aria_labels)}):")
        for a in aria_labels[:15]:
            print(f"  {a}")
        # Any strong/h3 names
        names = [x.get_text(strip=True) for x in p.select("h3, h4, strong")[:4]]
        print(f"names: {names}")
        # Images
        imgs = [x.get("src", "")[:80] for x in p.select("img[src]")[:3]]
        print(f"imgs: {imgs}")
        # Full text first 350 chars
        txt = p.get_text(separator=" | ", strip=True)[:350]
        print(f"text: {txt}")


if __name__ == "__main__":
    inspect(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fb_debug_m.html")
