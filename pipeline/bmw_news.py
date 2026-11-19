#!/usr/bin/env python3
"""Scrape press releases from the BMW Group Newsroom (global) via its RSS feed.

Usage:
    python3 bmw_news.py                  # print latest headlines
    python3 bmw_news.py --limit 5
    python3 bmw_news.py -o news.json     # full data as JSON
    python3 bmw_news.py -o news.csv      # full data as CSV

If the output file already exists, it is treated as the existing corpus:
items already in it (matched by id, falling back to url) are skipped and
only new items are added. Use --fresh to overwrite instead.
"""
import os
import argparse
import csv
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

FEED_URL = "https://newsroom.bmwgroup.com/global/feed/en"
PP = "{http://www.presspage.com/rss/}"
BLOCK_TAGS = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol", "table"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html):
    p = _TextExtractor()
    p.feed(html or "")
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n\n", text).strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; news-scraper)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse(xml_bytes):
    items = []
    for it in ET.fromstring(xml_bytes).iterfind("channel/item"):
        get = lambda tag: (it.findtext(tag) or "").strip()
        text = html_to_text(get("description"))
        pub = get("pubDate")
        items.append({
            "id": get(PP + "caseid"),
            "title": get("title"),
            "url": get("link"),
            "published": parsedate_to_datetime(pub).isoformat() if pub else "",
            "tags": [t.strip() for t in get("category").split(",") if t.strip()],
            "image": get(PP + "imageOriginal") or get(PP + "image"),
            "summary": text[:300],
            "text": text,
        })
    return items


def item_key(item):
    return item.get("id") or item.get("url")


def load_corpus(path):
    """Load an existing corpus file (.json or .csv); return [] if missing."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    if path.endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["tags"] = [t.strip() for t in (r.get("tags") or "").split(",") if t.strip()]
        return rows
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="Scrape BMW Group Newsroom press releases")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("-o", "--output", help="output file (.json or .csv)")
    ap.add_argument("--no-text", action="store_true", help="omit full article text")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore an existing output file and overwrite it")
    args = ap.parse_args()

    items = parse(fetch(FEED_URL))[: args.limit]
    if args.no_text:
        for i in items:
            i.pop("text")

    if not args.output:
        for i in items:
            print(f"{i['published'][:10]} | {i['title']}\n           {i['url']}")
        return

    corpus = [] if args.fresh else load_corpus(args.output)
    seen = {item_key(i) for i in corpus}
    new = [i for i in items if item_key(i) not in seen]
    skipped = len(items) - len(new)
    items = sorted(corpus + new, key=lambda i: i.get("published", ""), reverse=True)
    print(f"Existing corpus: {len(corpus)} items; feed: {len(new) + skipped} items "
          f"({skipped} already downloaded, {len(new)} new)", file=sys.stderr)

    if args.output.endswith(".csv"):
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            fields = list(dict.fromkeys(k for i in items for k in i)) or ["id"]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for i in items:
                w.writerow({**i, "tags": ", ".join(i["tags"])})
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} items to {args.output} ({len(new)} new)", file=sys.stderr)


if __name__ == "__main__":
    main()
