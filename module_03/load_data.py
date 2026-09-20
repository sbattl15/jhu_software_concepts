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

MIGRATE_SQL = """
ALTER TABLE applicants DROP CONSTRAINT IF EXISTS applicants_gpa_check;
"""

INSERT_SQL = """
INSERT INTO applicants (
    program, comments, date_added, url, status, term,
    us_or_international, gpa, gre, gre_v, gre_aw, degree,
    llm_generated_program, llm_generated_university
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (url) DO UPDATE SET
    program                  = EXCLUDED.program,
    comments                 = EXCLUDED.comments,
    date_added               = EXCLUDED.date_added,
    status                   = EXCLUDED.status,
    term                     = EXCLUDED.term,
    us_or_international      = EXCLUDED.us_or_international,
    gpa                      = EXCLUDED.gpa,
    gre                      = EXCLUDED.gre,
    gre_v                    = EXCLUDED.gre_v,
    gre_aw                   = EXCLUDED.gre_aw,
    degree                   = EXCLUDED.degree,
    llm_generated_program    = EXCLUDED.llm_generated_program,
    llm_generated_university = EXCLUDED.llm_generated_university;
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
            # 2. Create table structure (a no-op if it already exists)...
            cur.execute(CREATE_TABLE_SQL)

            # ...then bring an already-existing table's schema up to date,
            # in case it was created by an older version of this script.
            cur.execute(MIGRATE_SQL)

            # 3. Bulk upsert. executemany is plenty fast for ~40k rows;
            # ON CONFLICT (url) DO UPDATE makes reruns idempotent (no
            # UNIQUE-constraint failures on urls already loaded) while
            # still refreshing every column -- including gre/gre_v/gre_aw
            # -- for rows that were loaded before the JSON had that data.
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