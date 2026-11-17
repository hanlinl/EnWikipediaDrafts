#!/usr/bin/env python3
"""Analyse the references in the drafts listed in data/drafts.json.

Fetches current wikitext (50 pages per request) for every non-deleted draft, parses <ref>
tags, cite templates and URLs, and writes data/refs.csv (per draft) and data/refs_summary.json.
"""
import collections, csv, json, os, re, sys, urllib.parse
import collect
from collect import DATA, page_states

REF = re.compile(r"<ref\b[^>/]*?(?:/>|>(.*?)</ref>)", re.S | re.I)
REF_ANY = re.compile(r"<ref\b", re.I)
URL = re.compile(r"https?://[^\s|\]<>}\"']+", re.I)
CITE = re.compile(r"\{\{\s*(cite\s+\w+|citation|sfn|harvnb|webarchive)\b", re.I)
DOI = re.compile(r"\bdoi\s*=\s*10\.\d{4,}|doi\.org/10\.", re.I)
ISBN = re.compile(r"\bisbn\s*=\s*[\d-]{9,}|\bISBN[ :]+[\d-]{9,}", re.I)
# strings that chatbots leave behind in pasted output
AI_MARKS = {
    "utm_source=chatgpt": re.compile(r"utm_source=(chatgpt|openai|copilot|perplexity|claude|gemini)", re.I),
    "oaicite/contentReference": re.compile(r"oaicite|contentReference|\[oaicite|turn\d+(search|view|news)\d+", re.I),
    "markdown bold/headings": re.compile(r"(\*\*[^*\n]{3,}\*\*)|(^#{1,3} \S)", re.M),
}
SOCIAL = {"linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com",
          "youtu.be", "tiktok.com", "medium.com", "wordpress.com", "blogspot.com", "substack.com"}


def domain(u):
    h = urllib.parse.urlsplit(u).netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def analyse(text):
    refs = [m.group(0) for m in REF.finditer(text)]
    urls = URL.findall(text)
    doms = [domain(u) for u in urls]
    wp = [d for d in doms if d.endswith("wikipedia.org") or d.endswith("wikimedia.org")]
    out = {
        "chars": len(text),
        "n_ref_tags": len(REF_ANY.findall(text)),
        "n_urls": len(urls),
        "n_domains": len(set(doms)),
        "n_cite_templates": len(CITE.findall(text)),
        "n_doi": len(DOI.findall(text)),
        "n_isbn": len(ISBN.findall(text)),
        "n_wikipedia_links": len(wp),
        "n_social_or_blog": sum(any(d == s or d.endswith("." + s) for s in SOCIAL) for d in doms),
        "bare_url_refs": sum(1 for r in refs if re.fullmatch(r"<ref[^>]*>\s*\[?https?://[^\s\]<]*\]?\s*</ref>", r, re.I)),
        "has_reflist": bool(re.search(r"\{\{\s*reflist|<references", text, re.I)),
    }
    for k, rx in AI_MARKS.items():
        out[k] = len(rx.findall(text))
    return out, doms


def main():
    known = json.load(open(os.path.join(DATA, "drafts.json")))
    live = [r for r in known.values() if r.get("status") != "deleted"]
    titles = [r.get("current_title", r["title"]) for r in live]
    states = page_states(titles)
    rows, all_doms = [], collections.Counter()
    for r, t in zip(live, titles):
        st = states.get(t)
        if not st or st["missing"] or st["redirect"] or not st["text"]:
            continue
        a, doms = analyse(st["text"])
        all_doms.update(set(doms))  # count each domain once per draft
        rows.append({"title": r["title"], "status": r["status"], "created": r["created"], **a})
    cols = list(rows[0])
    with open(os.path.join(DATA, "refs.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    n = len(rows)
    pct = lambda k: round(100 * sum(1 for x in rows if x[k] > 0) / n, 1)
    nref = sorted(x["n_ref_tags"] for x in rows)
    summary = {
        "drafts_analysed": n,
        "no_ref_tags_pct": round(100 * sum(1 for x in rows if x["n_ref_tags"] == 0) / n, 1),
        "median_ref_tags": nref[n // 2], "mean_ref_tags": round(sum(nref) / n, 1),
        "ref_count_buckets": {"0": sum(1 for v in nref if v == 0), "1-4": sum(1 for v in nref if 1 <= v <= 4),
                              "5-14": sum(1 for v in nref if 5 <= v <= 14), "15+": sum(1 for v in nref if v >= 15)},
        "with_cite_templates_pct": pct("n_cite_templates"), "with_doi_pct": pct("n_doi"),
        "with_isbn_pct": pct("n_isbn"), "with_wikipedia_links_pct": pct("n_wikipedia_links"),
        "with_social_or_blog_pct": pct("n_social_or_blog"), "with_bare_url_refs_pct": pct("bare_url_refs"),
        "with_reflist_pct": pct("has_reflist"),
        "ai_markers_pct": {k: pct(k) for k in AI_MARKS},
        "top_domains_by_drafts": all_doms.most_common(40),
    }
    json.dump(summary, open(os.path.join(DATA, "refs_summary.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
