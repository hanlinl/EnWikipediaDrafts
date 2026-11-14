#!/usr/bin/env python3
"""Collect English Wikipedia drafts (Draft: namespace) that contain the AI-use
disclosure comment:

    In preparing this draft, I disclose that AI assistance was used as follows:

and were created on/after a cutoff date (default 2026-09-29).

Uses only the public MediaWiki API (CirrusSearch `insource:` + revision history).
Results are merged into data/drafts.json and data/drafts.csv, so reruns are incremental.
"""
import argparse, csv, json, os, sys, time, urllib.parse, urllib.request

API = "https://en.wikipedia.org/w/api.php"
UA = "EnWikipediaDraftsCollector/0.1 (research project; contact via repo)"
PHRASE = "AI assistance was used as follows"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def api(**params):
    params.update(format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def search_drafts(phrase):
    """Yield every Draft-namespace page whose wikitext contains the phrase."""
    cont = {}
    while True:
        d = api(action="query", list="search", srsearch=f'insource:"{phrase}"',
                srnamespace=118, srlimit=50, srprop="", **cont)
        for hit in d["query"]["search"]:
            yield hit["title"]
        if "continue" not in d:
            return
        cont = d["continue"]


def first_revision(title):
    d = api(action="query", prop="revisions", titles=title, rvdir="newer",
            rvlimit=1, rvprop="timestamp|user", )
    p = d["query"]["pages"][0]
    if p.get("missing") or "revisions" not in p:
        return None
    r = p["revisions"][0]
    return r["timestamp"], r["user"]


def disclosure_text(title):
    """Return the disclosure comment text from current wikitext, verifying the match."""
    d = api(action="query", prop="revisions", titles=title, rvprop="content",
            rvslots="main")
    p = d["query"]["pages"][0]
    if "revisions" not in p:
        return None
    text = p["revisions"][0]["slots"]["main"]["content"]
    i = text.find(PHRASE)
    if i < 0:
        return None  # removed since indexing, or search false positive
    start = text.rfind("<!--", 0, i)
    end = text.find("-->", i)
    snippet = text[start if start >= 0 else i: end + 3 if end >= 0 else i + 300]
    return " ".join(snippet.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-29", help="creation date cutoff (UTC, inclusive)")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "drafts.json")
    known = json.load(open(path)) if os.path.exists(path) else {}

    for title in search_drafts(PHRASE):
        if title in known:
            continue
        fr = first_revision(title)
        if not fr or fr[0][:10] < a.since:
            continue
        text = disclosure_text(title)
        if text is None:
            continue
        known[title] = {
            "title": title,
            "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            "created": fr[0], "creator": fr[1], "disclosure": text,
        }
        print("+", title, fr[0], file=sys.stderr)

    rows = sorted(known.values(), key=lambda r: r["created"])
    json.dump(known, open(path, "w"), indent=1, ensure_ascii=False)
    with open(os.path.join(DATA, "drafts.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["created", "title", "creator", "url", "disclosure"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})
    print(f"{len(rows)} drafts total", file=sys.stderr)


if __name__ == "__main__":
    main()
