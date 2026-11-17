# EnWikipediaDrafts

Collects English Wikipedia drafts (namespace `Draft:`) created on/after 2026-09-29 whose
wikitext contains the AI-disclosure comment
"In preparing this draft, I disclose that AI assistance was used as follows:".

    python3 collect.py [--since YYYY-MM-DD]

Output: `data/drafts.json` and `data/drafts.csv` (merged on rerun). Requires network access
to `en.wikipedia.org`. Stdlib only.

Caveats: relies on CirrusSearch `insource:` indexing (can lag minutes–hours); drafts that
have been moved to mainspace or deleted drop out of the Draft namespace.

## Status tracking
Each run re-checks every known draft and records `status`: `pending`, `declined`, `rejected`,
`draft (not submitted)`, `approved` (moved to mainspace; `current_title` is the article),
`deleted` (date/reason/admin from the deletion log), `redirect`, or `moved elsewhere`.
Accepted drafts are also discovered via a mainspace search. Deleted pages' text can't be read,
so run regularly: a draft deleted before its first sighting is not recorded.

## Submission tracking and regular runs
Each draft record has `submitted_at` (first revision with `{{AfC submission}}` pending, i.e. when it was
submitted for review) and `disclosed_at` (first revision containing the disclosure), taken from the draft's
history. New drafts get these when first seen; older records are backfilled `--backfill N` (default 40) per run.
`collect.py` is incremental and is run hourly by a scheduled task (fresh session -> checkout of this branch ->
`python3 collect.py` -> commit and push `data/`).
