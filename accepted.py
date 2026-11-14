#!/usr/bin/env python3
"""Find drafts accepted (moved Draft: -> mainspace) on/after a cutoff, then scan each
article's full revision history for the AI-disclosure phrase.

Stage 1: move log (letype=move, source ns 118, target ns 0) -> data/accepted_moves.json
Stage 2: per article, walk all revisions, record first/last revision containing the
         phrase, whether it is still present, and who removed it.
Resumable: results are checkpointed in data/accepted.json; matches go to data/accepted.csv.
"""
import argparse, csv, json, os, sys, time, urllib.parse
import collect
from collect import api, DATA, PHRASE

NORM = " ".join(PHRASE.lower().split())
MAX_REVS = 1000


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fetch_moves(since):
    cont, moves = {}, []
    while True:
        d = api(action="query", list="logevents", letype="move", lenamespace=118,
                lelimit=500, leend=since + "T00:00:00Z",
                leprop="title|timestamp|user|comment|details", **cont)
        for e in d["query"]["logevents"]:
            p = e.get("params") or {}
            if p.get("target_ns") == 0 and p.get("target_title"):
                moves.append({"draft": e["title"], "article": p["target_title"],
                              "moved": e["timestamp"], "mover": e.get("user", ""),
                              "move_comment": e.get("comment", "")})
        print(f"  moves so far: {len(moves)}", file=sys.stderr)
        if "continue" not in d:
            return moves
        cont = d["continue"]


def has_phrase(rev):
    text = (rev.get("slots", {}).get("main", {}) or {}).get("content", "")
    return NORM in " ".join(text.lower().split()), text


def snippet(text):
    i = " ".join(text.split()).lower().find(NORM)
    flat = " ".join(text.split())
    if i < 0:
        return ""
    lead = flat.lower().rfind("in preparing this draft", max(0, i - 120), i)
    start = lead if lead >= 0 else i
    return flat[start:start + 400]


def scan_history(title):
    """Return (status, revisions oldest->newest, truncated). status: ok|missing|redirect."""
    revs, cont, truncated = [], {}, False
    while True:
        d = api(action="query", prop="info|revisions", titles=title,
                rvprop="ids|timestamp|user|comment|content", rvslots="main",
                rvlimit=50, **cont)
        p = d["query"]["pages"][0]
        if p.get("missing"):
            return "missing", [], False
        if p.get("redirect"):
            return "redirect", [], False
        revs.extend(p.get("revisions", []))
        if "continue" not in d:
            break
        if len(revs) >= MAX_REVS:
            truncated = True
            break
        cont = d["continue"]
    revs.reverse()  # oldest first
    return "ok", revs, truncated


def analyse(move):
    title, hops = move["article"], 0
    status, revs, trunc = scan_history(title)
    while status in ("missing", "redirect") and hops < 3:  # article later renamed?
        nxt = [m for m in collect.log_events(title, "move") if (m.get("params") or {}).get("target_title")]
        if not nxt:
            break
        title = nxt[0]["params"]["target_title"]
        hops += 1
        status, revs, trunc = scan_history(title)
    rec = dict(move, current_title=title, page_status=status, checked=now())
    if status != "ok":
        rec.update(has_disclosure=False)  # deleted/unreadable: cannot tell
        return rec
    flags = [has_phrase(r) for r in revs]
    idx = [i for i, (h, _) in enumerate(flags) if h]
    rec.update(created=revs[0]["timestamp"] if not trunc else "", n_revs=len(revs),
               truncated=trunc, has_disclosure=bool(idx), n_revs_with_phrase=len(idx))
    if idx:
        f, l = idx[0], idx[-1]
        rec.update(
            url="https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            first_rev_ts=revs[f]["timestamp"], first_rev_user=revs[f].get("user", ""),
            last_rev_ts=revs[l]["timestamp"], present_now=(l == len(revs) - 1),
            disclosure=snippet(flags[l][1]))
        if l < len(revs) - 1:
            r = revs[l + 1]
            rec.update(removed_ts=r["timestamp"], removed_by=r.get("user", ""),
                       removed_comment=r.get("comment", ""), removed_revid=r.get("revid"))
    return rec


def save(results):
    p = os.path.join(DATA, "accepted.json")
    json.dump(results, open(p + ".tmp", "w"), indent=1, ensure_ascii=False)
    os.replace(p + ".tmp", p)
    cols = ["moved", "article", "current_title", "url", "draft", "mover", "move_comment",
            "created", "first_rev_ts", "first_rev_user", "present_now", "removed_ts",
            "removed_by", "removed_comment", "n_revs", "truncated", "disclosure"]
    rows = sorted((r for r in results.values() if r.get("has_disclosure")), key=lambda r: r["moved"])
    with open(os.path.join(DATA, "accepted.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-29")
    ap.add_argument("--limit", type=int, default=0, help="only scan this many new articles (testing)")
    ap.add_argument("--refresh-moves", action="store_true")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    mp = os.path.join(DATA, "accepted_moves.json")
    if os.path.exists(mp) and not a.refresh_moves:
        moves = json.load(open(mp))
    else:
        moves = fetch_moves(a.since)
        json.dump(moves, open(mp, "w"), indent=1)
    print(f"{len(moves)} drafts moved to mainspace since {a.since}", file=sys.stderr)

    rp = os.path.join(DATA, "accepted.json")
    results = json.load(open(rp)) if os.path.exists(rp) else {}
    todo = [m for m in moves if m["article"] not in results]
    if a.limit:
        todo = todo[:a.limit]
    for n, m in enumerate(todo, 1):
        rec = analyse(m)
        results[m["article"]] = rec
        tag = "MATCH" if rec.get("has_disclosure") else rec["page_status"]
        print(f"[{n}/{len(todo)}] {tag} {m['article']}", file=sys.stderr)
        if n % 20 == 0:
            save(results)
    save(results)
    hits = sum(bool(r.get("has_disclosure")) for r in results.values())
    print(f"done: {len(results)} articles scanned, {hits} with the disclosure in history", file=sys.stderr)


if __name__ == "__main__":
    main()
