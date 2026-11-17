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
                srnamespace="0|118", srlimit=50, srprop="", srsort="create_timestamp_desc", **cont)
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


NORM = " ".join(PHRASE.lower().split())


def snippet(text):
    """Disclosure text from wikitext: lead-in sentence through end of the comment/template."""
    flat = " ".join(text.split())
    i = flat.lower().find(NORM)
    if i < 0:
        return None
    lead = flat.lower().rfind("in preparing this draft", max(0, i - 120), i)
    start = lead if lead >= 0 else i
    end = flat.find("-->", i)
    return flat[start: end if 0 <= end - i < 2000 else i + 400]


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
    lead = text.rfind("In preparing this draft", max(0, i - 120), i)
    start = lead if lead >= 0 else i
    end = text.find("-->", i)
    snippet = text[start: end if 0 <= end - i < 2000 else i + 400]
    return " ".join(snippet.split())


AFC = __import__("re").compile(r"\{\{\s*AfC submission\s*\|([^|}]*)([^}]*)", __import__("re").I)
AFC_CODES = {"": "pending", "t": "pending (under review)", "d": "declined", "r": "rejected"}


def log_events(title, letype):
    d = api(action="query", list="logevents", letitle=title, letype=letype,
            lelimit=20, leprop="type|user|timestamp|comment|details")
    return d["query"]["logevents"]


def _st(p):
    text = ""
    if p.get("revisions"):
        text = (p["revisions"][0].get("slots", {}).get("main", {}) or {}).get("content", "")
    return {"missing": bool(p.get("missing")), "redirect": bool(p.get("redirect")),
            "ns": p.get("ns"), "text": text}


def page_state(title):
    d = api(action="query", prop="info|revisions", titles=title, rvprop="content",
            rvslots="main")
    return _st(d["query"]["pages"][0])


def page_states(titles):
    """Current state of many titles (<=50 per request). Returns {title: state}."""
    out = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        d = api(action="query", prop="info|revisions", titles="|".join(chunk),
                rvprop="content", rvslots="main")
        alias = {n["from"]: n["to"] for n in d["query"].get("normalized", [])}
        by_title = {p["title"]: _st(p) for p in d["query"]["pages"]}
        for t in chunk:
            if alias.get(t, t) in by_title:
                out[t] = by_title[alias.get(t, t)]
    return out


def resolve_status(rec, st0=None):
    """Work out where a draft is now. Returns dict of status fields (follows moves).
    st0: optional pre-fetched state of the first title (saves a request)."""
    title = rec.get("current_title", rec["title"])
    for hop in range(6):
        st = st0 if (hop == 0 and st0 is not None) else page_state(title)
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


def save(known):
    path = os.path.join(DATA, "drafts.json")
    rows = sorted(known.values(), key=lambda r: r["created"])
    json.dump(known, open(path + ".tmp", "w"), indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    with open(os.path.join(DATA, "drafts.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["created", "title", "status", "status_date", "current_title", "detail", "creator", "url", "disclosure"], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-29", help="creation date cutoff (UTC, inclusive)")
    ap.add_argument("--lookback-days", type=int, default=14,
                    help="stop paging search results once a known draft older than this is reached "
                         "(0 = page all the way back to --since)")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "drafts.json")
    known = json.load(open(path)) if os.path.exists(path) else {}
    stamp = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 1) refresh status of known drafts, 50 per request (deleted drafts stay deleted)
    live = [r for r in known.values() if r.get("status") != "deleted"]
    states = page_states([r.get("current_title", r["title"]) for r in live])
    for rec in live:
        t = rec.get("current_title", rec["title"])
        rec.update(resolve_status(rec, states.get(t)))
        rec["status_checked"] = stamp()
    save(known)
    print(f"refreshed {len(live)} known drafts", file=sys.stderr)

    # 2) discover new drafts: search results arrive newest-created first, content included
    claimed = {r.get("current_title") for r in known.values()} | set(known)
    horizon = ""
    if a.lookback_days:
        horizon = time.strftime("%Y-%m-%d", time.gmtime(time.time() - a.lookback_days * 86400))
    cont, done, new = {}, False, 0
    while not done:
        d = api(action="query", generator="search", gsrsearch=f'insource:"{PHRASE}"',
                gsrnamespace="0|118", gsrlimit=50, gsrsort="create_timestamp_desc",
                prop="info|revisions", rvprop="content", rvslots="main", **cont)
        pages = sorted(d.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        for p in pages:
            title = p["title"]
            if title in claimed:
                rec = known.get(title)
                if horizon and rec and rec["created"][:10] < horizon:
                    done = True
                    break
                continue
            st = _st(p)
            text = st["text"]
            i = " ".join(text.split()).lower().find(NORM)
            if st["missing"] or st["redirect"] or i < 0:
                continue
            fr = first_revision(title)
            if not fr:
                continue
            if fr[0][:10] < a.since:
                done = True  # newest-first: everything after is older
                break
            rec = {"title": title,
                   "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                   "created": fr[0], "creator": fr[1], "disclosure": snippet(text),
                   "first_seen": stamp()}
            rec.update(resolve_status(rec, st))
            known[title] = rec
            claimed.add(title)
            new += 1
            print("+", title, rec["status"], file=sys.stderr)
            save(known)
        if "continue" not in d:
            break
        cont = d["continue"]

    save(known)
    print(f"{new} new; {len(known)} drafts total", file=sys.stderr)


if __name__ == "__main__":
    main()
