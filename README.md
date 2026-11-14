# EnWikipediaDrafts

Collects English Wikipedia drafts (namespace `Draft:`) created on/after 2026-09-29 whose
wikitext contains the AI-disclosure comment
"In preparing this draft, I disclose that AI assistance was used as follows:".

    python3 collect.py [--since YYYY-MM-DD]

Output: `data/drafts.json` and `data/drafts.csv` (merged on rerun). Requires network access
to `en.wikipedia.org`. Stdlib only.

Caveats: relies on CirrusSearch `insource:` indexing (can lag minutes–hours); drafts that
have been moved to mainspace or deleted drop out of the Draft namespace.
