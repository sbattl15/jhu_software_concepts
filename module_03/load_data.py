from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import psycopg
from psycopg import sql

# --------------------------------------------------------------------------
# Parsing helpers — the source JSON stores several fields as free-form
# strings ("Added on Feb 17, 2026", "GPA 3.85", "GRE 166", "GRE V 160",
# "GRE AW 4.5"). We extract typed values out of those strings so the
# database columns are actually queryable (date ranges, GPA/GRE
# comparisons, etc.) instead of storing everything as opaque text.

DATE_ADDED_RE = re.compile(r"^Added on (?P<date>.+)$")
STATUS_RE = re.compile(
    r"^(?P<type>Accepted|Rejected|Interview|Wait listed) on (?P<date>.+)$"
)
GPA_RE = re.compile(r"^GPA\s+(?P<value>[\d.]+)$")

# The source JSON (llm_extend_applicant_data.json) has no dedicated GRE
# fields at all — unlike GPA, there is no "GRE" / "GRE V" / "GRE AW" key.
# GRE scores show up only occasionally, buried in free-text `comments`
# (e.g. "GRE 154V/161Q/4.0AW", "Q166 V162 AW4.0", "No GRE submitted"), in
# no consistent format. Rather than guess at a regex for one-off prose and
# risk silently mis-assigning a number to the wrong subscore, gre/gre_v/
# gre_aw are left NULL. parse_score() is kept below (and wired up in
# to_row) so that if a future export of this data adds real "GRE"/"GRE V"/
# "GRE AW" keys, the loader picks them up automatically with no changes.
SCORE_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*$")

DATE_ADDED_FMT = "%b %d, %Y"  # e.g. "Feb 17, 2026"


def parse_date_added(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    m = DATE_ADDED_RE.match(value)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("date"), DATE_ADDED_FMT).date()
    except ValueError:
        return None


def parse_gpa(value: Optional[str]) -> Optional[float]:
    """Parses the "GPA <value>" strings, e.g. "GPA 3.85". Some entries use
    a weighted/non-4.0 scale (values up to ~4.9 appear in the data), so
    this does not clamp or reject those — it just returns whatever number
    was reported, keeping the column an honest float rather than silently
    dropping ~100+ legitimate GPA values or risking a CHECK-constraint
    failure that would abort the whole batch insert."""
    if not value:
        return None
    m = GPA_RE.match(value.strip())
    if not m:
        return None
    try:
        return float(m.group("value"))
    except ValueError:
        return None


def parse_score(value: Optional[str]) -> Optional[float]:
    """Extracts a trailing numeric score (GRE Quant/Verbal/AW) from a
    free-form string such as "GRE 166" or "GRE AW 4.5". Returns None
    (rather than raising) for missing or unparseable values so a record
    with no GRE scores never blocks the load."""
    if not value:
        return None
    m = SCORE_RE.search(value.strip())
    if not m:
        return None
    try:
        return float(m.group("value"))
    except ValueError:
        return None


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_records(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def to_row(rec: dict[str, Any]) -> tuple:
    return (
        rec.get("program"),
        clean_text(rec.get("comments")),
        parse_date_added(rec.get("date_added")),
        rec.get("url"),
        clean_text(rec.get("status")),
        rec.get("term"),
        rec.get("US/International"),
        parse_gpa(rec.get("GPA")),
        parse_score(rec.get("GRE")),
        parse_score(rec.get("GRE V")),
        parse_score(rec.get("GRE AW")),
        rec.get("Degree"),
        rec.get("llm-generated-program"),
        rec.get("llm-generated-university"),
    )


# --------------------------------------------------------------------------
# Database creation
#
# psycopg.connect(dbname=...) only ever connects to an EXISTING database —
# it can't create one. CREATE DATABASE also can't run inside a transaction
# block, so this connects to the "postgres" maintenance database with
# autocommit on, checks pg_database, and creates the target database only
# if it isn't there yet. The connecting role needs the CREATEDB privilege.


def create_database_if_missing(
    dbname: str, user: Optional[str], password: Optional[str], host: str, port: str
) -> None:
    with psycopg.connect(
        dbname="postgres",
        user=user,
        password=password,
        host=host,
        port=port,
        autocommit=True,  # required: CREATE DATABASE cannot run in a transaction
    ) as admin_conn:
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
                print(f"Created database '{dbname}'.")
            else:
                print(f"Database '{dbname}' already exists.")


# --------------------------------------------------------------------------
# Schema
#
# Matches the required "applicants" table exactly: p_id, program, comments,
# date_added, url, status, term, us_or_international, gpa, gre, gre_v,
# gre_aw, degree, llm_generated_program, llm_generated_university.

# Note: gpa has no CHECK range constraint — a small number of source
# entries report a weighted/non-4.0-scale GPA above 4.0 (up to ~4.9), and
# a per-row constraint violation would abort the entire executemany()
# batch insert below rather than just that one row.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS applicants (
    p_id                        SERIAL PRIMARY KEY,   -- unique identifier
    program                     TEXT NOT NULL,        -- University and Department/Program
    comments                    TEXT,                 -- applicant comments
    date_added                  DATE,                 -- date entry was added
    url                         TEXT NOT NULL UNIQUE, -- link to Grad Cafe entry (natural key)
    status                      TEXT,                 -- admission status
    term                        TEXT,                 -- intended start term
    us_or_international         TEXT,                 -- applicant nationality classification
    gpa                         REAL,                 -- applicant GPA
    gre                         REAL,                 -- GRE Quantitative score
    gre_v                       REAL,                 -- GRE Verbal score
    gre_aw                      REAL,                 -- GRE Analytical Writing score
    degree                      TEXT,                 -- degree type
    llm_generated_program       TEXT,                 -- LLM-generated department/program
    llm_generated_university    TEXT                  -- LLM-generated university
);

CREATE INDEX IF NOT EXISTS idx_applicants_university  ON applicants (llm_generated_university);
CREATE INDEX IF NOT EXISTS idx_applicants_program      ON applicants (llm_generated_program);
CREATE INDEX IF NOT EXISTS idx_applicants_term         ON applicants (term);
CREATE INDEX IF NOT EXISTS idx_applicants_degree       ON applicants (degree);
"""

INSERT_SQL = """
INSERT INTO applicants (
    program, comments, date_added, url, status, term,
    us_or_international, gpa, gre, gre_v, gre_aw, degree,
    llm_generated_program, llm_generated_university
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (url) DO NOTHING;
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load thegradcafe.com applicant result JSON into PostgreSQL."
    )
    parser.add_argument(
        "--json-file",
        type=Path,
        default=Path("llm_extend_applicant_data.json"),
        help="Path to the source JSON file (a list of applicant-result objects).",
    )
    parser.add_argument("--dbname", default=os.environ.get("PGDATABASE", "gradcafe_applications"))
    parser.add_argument("--user", default=os.environ.get("PGUSER", "postgres"))
    parser.add_argument(
        "--password",
        default=os.environ.get("PGPASSWORD"),
        help="Postgres password. Prefer setting the PGPASSWORD environment "
        "variable (or a ~/.pgpass file) over passing this flag or hardcoding "
        "it, so the password never ends up in shell history or source code.",
    )
    parser.add_argument("--host", default=os.environ.get("PGHOST", "localhost"))
    parser.add_argument("--port", default=os.environ.get("PGPORT", "5432"))
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="Create the target database first if it doesn't already exist "
        "(the connecting role needs the CREATEDB privilege).",
    )
    args = parser.parse_args()

    if args.create_db:
        create_database_if_missing(args.dbname, args.user, args.password, args.host, args.port)

    # 1. Load the JSON file into the program (plain Python objects)
    records = load_records(args.json_file)
    rows = [to_row(rec) for rec in records]

    conn = psycopg.connect(
        dbname=args.dbname,
        user=args.user,
        password=args.password,
        host=args.host,
        port=args.port,
    )

    try:
        with conn.cursor() as cur:
            # 2. Create table structure
            cur.execute(CREATE_TABLE_SQL)

            # 3. Bulk insert. executemany is plenty fast for ~40k rows;
            # ON CONFLICT (url) DO NOTHING makes reruns idempotent instead
            # of failing on the UNIQUE constraint.
            cur.executemany(INSERT_SQL, rows)

        conn.commit()
        print(f"Successfully uploaded {len(rows)} rows from JSON to PostgreSQL!")

    except Exception as e:
        conn.rollback()
        print(f"An error occurred: {e}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()