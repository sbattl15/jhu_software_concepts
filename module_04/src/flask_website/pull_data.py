from __future__ import annotations

"""
Part 9 pipeline: scrape -> clean -> standardize (LLM) -> load.

Run directly (``python pull_data.py``) or as a subprocess (this is what the
Flask app's "Pull Data" button does -- see app.py's ``start_pull_data``).

Steps:
  1. scrape.scrape_data()      -- fetch newly available Grad Cafe entries.
  2. clean.clean_data()    -- regex cleaner: turns each raw entry into
     a sparse dict of *formatted strings* ("GPA 3.70", "GRE 168",
     "Added on Feb 17, 2026", key "US/International", ...).
  3. _standardize_with_llm()   -- llm_standardize._call_llm() +
     _clean_row(): runs each row's combined "program" string through a
     local LLM (once per *distinct* string, then fans the cached result
     out to every row that shares it) to split/standardize it into
     llm-generated-program / llm-generated-university, and at the same
     time derives the rest of the typed fields (date_added as an ISO
     date, status split from "<Type> on <date>", gpa/gre as floats,
     us_or_international, degree) via the same helper.
  4. _to_applicant_row()       -- maps each fully-cleaned row onto the
     Applicant table's actual columns. Rows missing a required
     (NOT NULL) field -- program or url -- are skipped as "not usable".
  5. Bulk insert with ON CONFLICT (url) DO NOTHING, since url is the
     table's natural/unique key -- this is what makes a Pull Data click
     safe to repeat: already-known entries are silently skipped rather
     than duplicated.

Prints a final ``PULL_DATA_SUMMARY::{...}`` JSON line that app.py's
background watcher thread parses to build the status message shown on
the webpage.
"""

import json
import os
import sys
from datetime import date, datetime
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

import llm_standardize
import scrape
from clean import clean_data
from models import Applicant, get_session

# How many survey pages an interactive "Pull Data" click fetches. Kept
# small (unlike scrape.py's own ~2,000-page MAX_PAGES default, meant for a
# one-time full pull) since this runs synchronously in a background
# subprocess kicked off by a button click, and every distinct program
# string pulled in gets run through the local LLM below. Override with the
# PULL_DATA_MAX_PAGES env var.
PULL_DATA_MAX_PAGES = int(os.environ.get("PULL_DATA_MAX_PAGES", "25"))

# clean.py's _DEGREE_SUFFIX_RE captures whichever of these literal
# tokens GradCafe used; llm_standardize._clean_row() passes that token
# through unchanged as row["degree"]. orm_queries.py/query_data.py filter
# on the exact strings "PhD" and "Masters" (question_7/8/9), so incoming
# variants are normalized to match. Verify against `SELECT DISTINCT degree
# FROM applicants;` if your data uses different canonical values.
_DEGREE_NORMALIZE = {
    "master's": "Masters",
    "masters": "Masters",
    "ms": "Masters",
    "msc": "Masters",
    "ma": "Masters",
    "mba": "Masters",
    "meng": "Masters",
    "mfa": "Masters",
    "mph": "Masters",
    "phd": "PhD",
    "ph.d": "PhD",
    "ph.d.": "PhD",
    "doctorate": "PhD",
    "psyd": "PhD",
    "edd": "PhD",
}


def _normalize_degree(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = raw.strip().lower().replace("’", "'")
    return _DEGREE_NORMALIZE.get(key, raw.strip())


def _standardize_with_llm(cleaned_rows: list[dict]) -> None:
    """Adds llm-generated-program / llm-generated-university to each row
    in place (plus the other typed fields llm_standardize._clean_row
    derives: date_added, status, gpa, gre*, us_or_international, degree),
    by running each row's "program" string through the local LLM.

    Calls the model once per *distinct* program string and fans the
    cached result out to every row that shares it -- the same dedup
    strategy llm_standardize.py's own CLI path uses, since GradCafe data
    is full of exact repeats. If the model can't run at all (e.g. no
    network for the first-run model download), this still calls
    _clean_row() with an empty standardization result for every row, so
    the non-LLM fields (date/status/gpa/...) are derived either way --
    only llm-generated-program/-university end up empty for this run.
    """
    program_texts = [row.get("program") or "" for row in cleaned_rows]
    unique_texts = [t for t in dict.fromkeys(program_texts) if t]

    results_by_text: dict[str, dict] = {}
    if unique_texts:
        print(
            f"[pull_data] Standardizing {len(unique_texts):,} distinct program "
            f"string(s) with the local LLM (first run downloads the model; this "
            f"can take a while)..."
        )
        for i, text in enumerate(unique_texts, start=1):
            try:
                results_by_text[text] = llm_standardize._call_llm(text)
            except Exception as exc:
                print(
                    f"[pull_data] LLM standardization failed for one program "
                    f"string, leaving it un-standardized: {exc}",
                    file=sys.stderr,
                )
            if i % 50 == 0 or i == len(unique_texts):
                print(
                    f"[pull_data] LLM standardization: {i:,}/{len(unique_texts):,} "
                    f"distinct strings done"
                )

    empty_result = {"standardized_program": "", "standardized_university": ""}
    for row, text in zip(cleaned_rows, program_texts):
        llm_standardize._clean_row(row, results_by_text.get(text, empty_result))


def _to_applicant_row(row: dict) -> Optional[dict]:
    """Convert one row -- already run through clean_data() and then
    _standardize_with_llm() -- into Applicant column kwargs, or None if
    it's missing a required (NOT NULL) field."""
    url = (row.get("url") or "").strip()
    program = (row.get("program") or "").strip()
    if not url or not program:
        return None

    date_added: Optional[date] = None
    iso_date = row.get("date_added")
    if iso_date:
        try:
            date_added = date.fromisoformat(iso_date)
        except ValueError:
            date_added = None

    return {
        "program": program,
        "comments": row.get("comments") or None,
        "date_added": date_added,
        "url": url,
        "status": row.get("status") or None,
        "term": row.get("term") or None,
        "us_or_international": row.get("us_or_international") or None,
        "gpa": row.get("gpa"),
        "gre": row.get("gre"),
        "gre_v": row.get("gre_v"),
        "gre_aw": row.get("gre_aw"),
        "degree": _normalize_degree(row.get("degree")),
        "llm_generated_program": row.get("llm-generated-program") or None,
        "llm_generated_university": row.get("llm-generated-university") or None,
    }


def _load_rows(session, rows: list[dict]) -> int:
    """Bulk-insert rows, skipping any whose url already exists. Returns the
    number of rows actually inserted (i.e. genuinely new)."""
    if not rows:
        return 0
    stmt = pg_insert(Applicant).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["url"]).returning(Applicant.p_id)
    result = session.execute(stmt)
    inserted_ids = result.fetchall()
    session.commit()
    return len(inserted_ids)


def main() -> int:
    print(f"[pull_data] Starting Grad Cafe pull at {datetime.now().isoformat(timespec='seconds')}")

    try:
        raw_entries = scrape.scrape_data(max_pages=PULL_DATA_MAX_PAGES)
    except Exception as exc:
        print(f"[pull_data] Scrape failed: {exc}", file=sys.stderr)
        return 1

    print(f"[pull_data] Scraped {len(raw_entries):,} raw entrie(s). Cleaning...")

    try:
        cleaned_rows = clean_data(raw_entries)
    except Exception as exc:
        print(f"[pull_data] Cleaning failed: {exc}", file=sys.stderr)
        return 1

    try:
        _standardize_with_llm(cleaned_rows)
    except Exception as exc:
        print(
            f"[pull_data] LLM standardization step failed, continuing without "
            f"it: {exc}",
            file=sys.stderr,
        )

    rows = []
    skipped = 0
    for cleaned in cleaned_rows:
        row = _to_applicant_row(cleaned)
        if row is None:
            skipped += 1
            continue
        rows.append(row)

    print(
        f"[pull_data] {len(rows):,} usable entrie(s) after cleaning "
        f"({skipped:,} skipped: missing program/url)."
    )

    try:
        with get_session() as session:
            inserted = _load_rows(session, rows)
    except Exception as exc:
        print(f"[pull_data] Database load failed: {exc}", file=sys.stderr)
        return 1

    duplicates = len(rows) - inserted
    print(
        f"[pull_data] Load complete: {inserted:,} new record(s) added to the "
        f"database ({duplicates:,} were already present)."
    )

    # Machine-parseable summary line -- app.py's background watcher thread
    # looks for this prefix to build the status banner/message.
    print(
        "PULL_DATA_SUMMARY::"
        + json.dumps(
            {
                "raw": len(raw_entries),
                "usable": len(rows),
                "inserted": inserted,
                "duplicates": duplicates,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
