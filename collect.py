#!/usr/bin/env python3
"""Collect English Wikipedia drafts (Draft: namespace) that contain the AI-use
disclosure comment:

    In preparing this draft, I disclose that AI assistance was used as follows:

and were created on/after a cutoff date (default 2026-09-29).

Uses only the public MediaWiki API (CirrusSearch `insource:` + revision history).
Results are merged into data/drafts.json and data/drafts.csv, so reruns are incremental.
"""
import argparse, csv, json, os, sys, time, urllib.error, urllib.parse, urllib.request

API = "https://en.wikipedia.org/w/api.php"
UA = "EnWikipediaDraftsCollector/0.1 (research project; contact via repo)"
PHRASE = "AI assistance was used as follows"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


_last = [0.0]


def api(**params):
    params.update(format="json", formatversion="2", maxlag="5")
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(12):
        wait = 1.0 - (time.time() - _last[0])  # polite pacing: ~1 request/sec
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            if d.get("error", {}).get("code") == "maxlag":
                time.sleep(5)
                continue
            return d
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == 11:
                raise
            ra = e.headers.get("Retry-After", "")
            delay = max(int(ra) if ra.isdigit() else 0, 5) * (1 + attempt // 3)
            print(f"  HTTP {e.code}; waiting {delay}s", file=sys.stderr)
            time.sleep(delay)
        except Exception:
            if attempt == 11:
                raise
            time.sleep(min(2 ** attempt, 30))


def search_drafts(phrase):
    """Yield (ns, title) for every Draft/main page whose wikitext contains the phrase."""
    cont = {}
    while True:
        d = api(action="query", list="search", srsearch=f'insource:"{phrase}"',
                srnamespace="0|118", srlimit=50, srprop="", **cont)
        for hit in d["query"]["search"]:
            yield hit["ns"], hit["title"]
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


AFC = __import__("re").compile(r"\{\{\s*AfC submission\s*\|([^|}]*)([^}]*)", __import__("re").I)
AFC_CODES = {"": "pending", "t": "pending (under review)", "d": "declined", "r": "rejected"}


def log_events(title, letype):
    d = api(action="query", list="logevents", letitle=title, letype=letype,
            lelimit=20, leprop="type|user|timestamp|comment|details")
    return d["query"]["logevents"]


def page_state(title):
    d = api(action="query", prop="info|revisions", titles=title, rvprop="content",
            rvslots="main")
    p = d["query"]["pages"][0]
    text = ""
    if p.get("revisions"):
        text = p["revisions"][0]["slots"]["main"]["content"]
    return {"missing": bool(p.get("missing")), "redirect": bool(p.get("redirect")),
            "ns": p.get("ns"), "text": text}


def resolve_status(rec):
    """Work out where a draft is now. Returns dict of status fields (follows moves)."""
    title = rec.get("current_title", rec["title"])
    for _ in range(6):
        st = page_state(title)
        if st["missing"] or st["redirect"]:
            moves = [m for m in log_events(title, "move") if (m.get("params") or {}).get("target_title")]
            if moves:
                m = moves[0]
                title = m["params"]["target_title"]
                last_move = m
                continue
            if st["missing"]:
                dels = [e for e in log_events(title, "delete") if e.get("action") == "delete"]
                e = dels[0] if dels else {}
                return {"status": "deleted", "current_title": title,
                        "status_date": e.get("timestamp", ""),
                        "detail": f'{e.get("comment", "")} (by {e.get("user", "?")})' if e else "not found; no delete log entry"}
            return {"status": "redirect", "current_title": title, "status_date": "", "detail": ""}
        if st["ns"] == 0:
            return {"status": "approved", "current_title": title,
                    "status_date": locals().get("last_move", {}).get("timestamp", ""),
                    "detail": "now in mainspace"}
        if st["ns"] == 118:
            m = AFC.search(st["text"])
            if not m:
                return {"status": "draft (not submitted)", "current_title": title, "status_date": "", "detail": ""}
            code = m.group(1).strip().lower()
            reason = __import__("re").search(r"reason=([^|}]+)", m.group(2))
            return {"status": AFC_CODES.get(code, f"AfC:{code}"), "current_title": title,
                    "status_date": "", "detail": reason.group(1).strip() if reason else ""}
        return {"status": "moved elsewhere", "current_title": title, "status_date": "",
                "detail": f"namespace {st['ns']}"}
    return {"status": "unknown (move chain too long)", "current_title": title, "status_date": "", "detail": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-29", help="creation date cutoff (UTC, inclusive)")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "drafts.json")
    known = json.load(open(path)) if os.path.exists(path) else {}

    # 1) refresh status of everything already known (deleted drafts stay deleted)
    for rec in known.values():
        if rec.get("status") == "deleted":
            continue
        rec.update(resolve_status(rec))
        rec["status_checked"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 2) discover new drafts; Draft: hits first, then mainspace (accepted drafts)
    hits = sorted(search_drafts(PHRASE), key=lambda h: h[0] != 118)
    claimed = {r.get("current_title") for r in known.values()} | set(known)
    for ns, title in hits:
        if title in claimed:
            continue
        fr = first_revision(title)
        if not fr or fr[0][:10] < a.since:
            continue
        text = disclosure_text(title)
        if text is None:
            continue
        rec = {"title": title,
               "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
               "created": fr[0], "creator": fr[1], "disclosure": text,
               "first_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        rec.update(resolve_status(rec))
        known[title] = rec
        claimed.add(title)
        print("+", title, rec["status"], file=sys.stderr)

    rows = sorted(known.values(), key=lambda r: r["created"])
    json.dump(known, open(path, "w"), indent=1, ensure_ascii=False)
    with open(os.path.join(DATA, "drafts.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["created", "title", "status", "status_date", "current_title", "detail", "creator", "url", "disclosure"], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"{len(rows)} drafts total", file=sys.stderr)


if __name__ == "__main__":
    main()
