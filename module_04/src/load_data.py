"""Load cleaned GradCafe applicant data into PostgreSQL.

This module is the **database-loading** stage of the ETL pipeline. It reads
the LLM-extended JSON produced by the cleaning step, converts free-form
string fields (``"Added on Feb 17, 2026"``, ``"GPA 3.85"``, ``"GRE V 160"``)
into typed Python values, and bulk-upserts them into the ``applicants``
table.

The database is chosen by the ``DATABASE_URL`` environment variable (or
``--database-url``). If that isn't set, the standard libpq variables
(``PGDATABASE``, ``PGUSER``, ``PGPASSWORD``, ``PGHOST``, ``PGPORT``) are used,
and each can be overridden on the command line.

Example::

    python load_data.py --json-file llm_extend_applicant_data.json --create-db
"""

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


def libpq_url(url: str) -> str:
    """Convert a SQLAlchemy-style URL into one psycopg/libpq accepts.

    :param url: e.g. ``postgresql+psycopg://u:p@host:5432/db``.
    :returns: e.g. ``postgresql://u:p@host:5432/db``.
    :rtype: str

    >>> libpq_url("postgresql+psycopg://u:p@h/db")
    'postgresql://u:p@h/db'
    """
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def parse_date_added(value: Optional[str]) -> Optional[date]:
    """Parse an ``"Added on <Mon DD, YYYY>"`` string into a :class:`datetime.date`.

    :param value: Raw ``date_added`` string, e.g. ``"Added on Feb 17, 2026"``.
    :type value: str or None
    :returns: The parsed date, or ``None`` if the value is empty, does not
        match the expected prefix, or has an unparseable date.
    :rtype: datetime.date or None

    >>> parse_date_added("Added on Feb 17, 2026")
    datetime.date(2026, 2, 17)
    """
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
    """Parse a ``"GPA <value>"`` string, e.g. ``"GPA 3.85"``, into a float.

    Some entries use a weighted/non-4.0 scale (values up to ~4.9 appear in
    the data), so this does not clamp or reject those -- it returns whatever
    number was reported, keeping the column an honest float rather than
    silently dropping legitimate values or tripping a CHECK constraint that
    would abort the whole batch insert.

    :param value: Raw GPA string.
    :type value: str or None
    :returns: The numeric GPA, or ``None`` if missing or unparseable.
    :rtype: float or None
    """
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
    """Extract a trailing numeric GRE score from a free-form string.

    Works for Quant, Verbal and Analytical Writing, e.g. ``"GRE 166"``,
    ``"GRE V 160"`` or ``"GRE AW 4.5"``. Returns ``None`` (rather than
    raising) for missing or unparseable values so a record with no GRE
    scores never blocks the load.

    :param value: Raw score string.
    :type value: str or None
    :returns: The score as a float, or ``None``.
    :rtype: float or None
    """
    if not value:
        return None
    m = SCORE_RE.search(value.strip())
    if not m:
        return None
    # SCORE_RE only matches digits with an optional decimal part, so the
    # captured text is always a valid float.
    return float(m.group("value"))


def clean_text(value: Optional[str]) -> Optional[str]:
    """Strip surrounding whitespace and normalise empty strings to ``None``.

    :param value: Text to clean.
    :type value: str or None
    :returns: The stripped string, or ``None`` if it was ``None`` or blank.
    :rtype: str or None
    """
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_records(path: Path) -> list[dict[str, Any]]:
    """Read the source JSON file into a list of applicant dictionaries.

    :param path: Path to a JSON file containing a list of applicant objects.
    :type path: pathlib.Path
    :returns: The decoded list of records.
    :rtype: list[dict[str, Any]]
    :raises FileNotFoundError: If ``path`` does not exist.
    :raises json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def to_row(rec: dict[str, Any]) -> tuple:
    """Convert one JSON record into a parameter tuple for :data:`INSERT_SQL`.

    The tuple order matches the column list in :data:`INSERT_SQL`:
    ``program, comments, date_added, url, status, term,
    us_or_international, gpa, gre, gre_v, gre_aw, degree,
    llm_generated_program, llm_generated_university``.

    :param rec: A single applicant record from the JSON file.
    :type rec: dict[str, Any]
    :returns: A 14-element tuple of typed column values.
    :rtype: tuple
    """
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
    """Create the target database if it does not already exist.

    Connects to the ``postgres`` maintenance database with autocommit
    enabled (``CREATE DATABASE`` cannot run inside a transaction). The
    connecting role needs the ``CREATEDB`` privilege.

    :param dbname: Name of the database to create.
    :param user: PostgreSQL role name.
    :param password: Password for ``user`` (``None`` to rely on ``PGPASSWORD``
        or ``~/.pgpass``).
    :param host: Database host.
    :param port: Database port.
    :rtype: None
    """
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

#: DDL for the ``applicants`` table and its lookup indexes. Idempotent
#: (``IF NOT EXISTS``) so it is safe to run on every load.
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

#: Brings a table created by an older version of this script up to date
#: (drops a legacy GPA CHECK constraint that rejected weighted GPAs).
MIGRATE_SQL = """
ALTER TABLE applicants DROP CONSTRAINT IF EXISTS applicants_gpa_check;
"""

#: Parameterised upsert keyed on ``url``. Re-running the loader refreshes
#: every column of existing rows instead of failing on the UNIQUE constraint.
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
    """Command-line entry point: parse arguments and load the JSON file.

    Steps:

    1. Optionally create the database (``--create-db``).
    2. Read the JSON file and convert every record with :func:`to_row`.
    3. Run :data:`CREATE_TABLE_SQL` and :data:`MIGRATE_SQL`.
    4. Bulk-upsert all rows with :data:`INSERT_SQL` in a single transaction,
       rolling back on any error.

    :raises Exception: Re-raises any database error after rolling back.
    """
    parser = argparse.ArgumentParser(
        description="Load thegradcafe.com applicant result JSON into PostgreSQL."
    )
    parser.add_argument(
        "--json-file",
        type=Path,
        default=Path("llm_extend_applicant_data.json"),
        help="Path to the source JSON file (a list of applicant-result objects).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL URL, e.g. postgresql://user:pw@localhost:5432/db "
        "(default: $DATABASE_URL). Takes precedence over the --dbname/--user/"
        "--host/--port options.",
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

    if args.database_url:
        conn = psycopg.connect(libpq_url(args.database_url))
    else:
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