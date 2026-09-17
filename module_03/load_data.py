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
# strings ("Added on Feb 17, 2026", "Accepted on Apr 15", "GPA 3.85").
# We keep the raw string too, but also extract typed columns so the data
# is actually queryable (date ranges, GPA comparisons, status filters).

DATE_ADDED_RE = re.compile(r"^Added on (?P<date>.+)$")
STATUS_RE = re.compile(
    r"^(?P<type>Accepted|Rejected|Interview|Wait listed) on (?P<date>.+)$"
)
GPA_RE = re.compile(r"^GPA\s+(?P<value>[\d.]+)$")

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


def parse_status(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Returns (status_type, status_date_text). No year is present in the
    source status string (e.g. "Accepted on Apr 15"), and it isn't safe to
    infer one from date_added (decisions are sometimes logged well before
    or after the post date), so the month/day is kept as text rather than
    guessing a DATE."""
    if not value:
        return None, None
    m = STATUS_RE.match(value)
    if not m:
        return None, None
    return m.group("type"), m.group("date")


def parse_gpa(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = GPA_RE.match(value.strip())
    if not m:
        return None
    try:
        gpa = float(m.group("value"))
    except ValueError:
        return None
    return gpa if 0 <= gpa <= 4.0 else None


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_records(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def to_row(rec: dict[str, Any]) -> tuple:
    status_type, status_date_text = parse_status(rec.get("status"))
    return (
        rec.get("program"),
        rec.get("llm-generated-program"),
        rec.get("llm-generated-university"),
        rec.get("Degree"),
        clean_text(rec.get("comments")),
        parse_date_added(rec.get("date_added")),
        rec.get("url"),
        rec.get("status"),
        status_type,
        status_date_text,
        rec.get("term"),
        rec.get("US/International"),
        parse_gpa(rec.get("GPA")),
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

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS applicants (
    id                          SERIAL PRIMARY KEY,
    program                     TEXT NOT NULL,       -- raw "Program, University" string
    llm_generated_program       TEXT,                -- normalized program name
    llm_generated_university    TEXT,                -- normalized university name
    degree                      TEXT,                -- PhD, Masters, MFA, PsyD, Other, MBA, EdD, JD
    comments                    TEXT,
    date_added                  DATE,                -- parsed from "Added on <date>"
    url                         TEXT NOT NULL UNIQUE, -- thegradcafe.com result URL, natural key
    status_raw                  TEXT,                -- original "<Type> on <date>" string
    status_type                 TEXT CHECK (status_type IN ('Accepted', 'Rejected', 'Interview', 'Wait listed')),
    status_date_text            TEXT,                -- month/day only, no reliable year available
    term                        TEXT,                -- e.g. "Fall 2026"
    us_international            TEXT CHECK (us_international IN ('American', 'International')),
    gpa                         NUMERIC(3, 2) CHECK (gpa IS NULL OR (gpa >= 0 AND gpa <= 4.0))
);

CREATE INDEX IF NOT EXISTS idx_applicants_university  ON applicants (llm_generated_university);
CREATE INDEX IF NOT EXISTS idx_applicants_program      ON applicants (llm_generated_program);
CREATE INDEX IF NOT EXISTS idx_applicants_term         ON applicants (term);
CREATE INDEX IF NOT EXISTS idx_applicants_status_type  ON applicants (status_type);
CREATE INDEX IF NOT EXISTS idx_applicants_degree       ON applicants (degree);
"""

INSERT_SQL = """
INSERT INTO applicants (
    program, llm_generated_program, llm_generated_university, degree,
    comments, date_added, url, status_raw, status_type, status_date_text,
    term, us_international, gpa
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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