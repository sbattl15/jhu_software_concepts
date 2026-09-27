"""Answer the assignment's analysis questions with raw SQL.

This module is part of the **database layer**. It connects to PostgreSQL
with :mod:`psycopg`, runs one query per assignment question against the
``applicants`` table (created by :mod:`load_data`), and prints the answers.

The database is chosen by the ``DATABASE_URL`` environment variable. If it
isn't set, the libpq variables ``PGDATABASE`` (default
``gradcafe_applications``), ``PGUSER`` (default ``postgres``),
``PGPASSWORD``, ``PGHOST`` (default ``localhost``) and ``PGPORT`` (default
``5432``) are used.

Example::

    python query_data.py

All work happens inside :func:`main`, so importing this module (e.g. from
Sphinx autodoc or pytest) does **not** open a database connection.
"""

import os

import psycopg


def libpq_url(url: str) -> str:
    """Convert a SQLAlchemy-style URL into one psycopg/libpq accepts.

    :param url: e.g. ``postgresql+psycopg://u:p@host:5432/db``.
    :returns: e.g. ``postgresql://u:p@host:5432/db``.
    :rtype: str

    >>> libpq_url("postgresql+psycopg://u:p@h/db")
    'postgresql://u:p@h/db'
    """
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def get_connection() -> psycopg.Connection:
    """Open a PostgreSQL connection from ``DATABASE_URL`` or the ``PG*`` variables.

    :returns: An open psycopg connection. The caller must close it.
    :rtype: psycopg.Connection
    :raises psycopg.OperationalError: If the database is unreachable.
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg.connect(libpq_url(database_url))
    return psycopg.connect(
        dbname=os.environ.get("PGDATABASE", "gradcafe_applications"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
    )


def run_queries(conn: psycopg.Connection) -> dict:
    """Run every analysis query and return the raw answers.

    :param conn: An open psycopg connection.
    :returns: A dict with keys ``count1``, ``percent_international``,
        ``avg_gpa``, ``avg_gre_q``, ``avg_gre_v``, ``avg_gre_aw``,
        ``avg_gpa_american_fall2026``, ``fall_2025_acceptance_pct``,
        ``avg_gpa_accepted_fall2026``, ``jhu_ms_cs_count``,
        ``original_field_count``, ``llm_field_count``,
        ``gpa_bucket_rows`` (list of ``(bucket, total, pct)``) and
        ``term_count_rows`` (list of ``(term, count)``).
    :rtype: dict
    """
    with conn.cursor() as cur:
        # Question 1
        cur.execute("SELECT count(*) FROM applicants WHERE term = 'Fall 2026';")
        count1 = cur.fetchone()[0]

        # Question 2
        cur.execute(
            """
            SELECT COUNT(*) FILTER (WHERE us_or_international = 'International') AS international_count, COUNT(*) FILTER (
                    WHERE us_or_international IS NOT NULL AND btrim(us_or_international) <> ''
                ) AS classified_count
            FROM applicants;
            """
        )
        international_count, classified_count = cur.fetchone()
        percent_international = (
            round(100.0 * international_count / classified_count, 2))

        # Question 3
        cur.execute(
            """
            SELECT
                ROUND(AVG(gpa)::numeric, 2)    AS avg_gpa,
                ROUND(AVG(gre)::numeric, 2)    AS avg_gre_q,
                ROUND(AVG(gre_v)::numeric, 2)  AS avg_gre_v,
                ROUND(AVG(gre_aw)::numeric, 2) AS avg_gre_aw
            FROM applicants;
            """
        )
        avg_gpa, avg_gre_q, avg_gre_v, avg_gre_aw = cur.fetchone()

        # Question 4
        cur.execute(
            """
            SELECT ROUND(AVG(gpa)::numeric, 2) AS avg_gpa_american_fall2026
            FROM applicants
            WHERE term = 'Fall 2026'
              AND us_or_international = 'American'
              AND gpa IS NOT NULL;
            """,
        )
        avg_gpa_american_fall2026 = cur.fetchone()[0]

        # Question 5
        cur.execute(
            """
            SELECT ROUND(
                COUNT(*) FILTER (WHERE term = 'Fall 2025' AND status LIKE 'Accepted%%')::numeric
                * 100.0
                / NULLIF(COUNT(*) FILTER (WHERE term = 'Fall 2025'), 0),
                2
            ) AS fall_2025_acceptance_pct
            FROM applicants;
            """,
        )
        fall_2025_acceptance_pct = cur.fetchone()[0]

        # Question 6
        cur.execute(
            """
            SELECT ROUND(AVG(gpa)::numeric, 2) AS avg_gpa_accepted_fall2026
            FROM applicants
            WHERE term = 'Fall 2025'
              AND status LIKE 'Accepted%%'
              AND gpa IS NOT NULL;
            """,
        )
        avg_gpa_accepted_fall2026 = cur.fetchone()[0]

        # Question 7
        cur.execute(
            """
            SELECT count(*) AS jhu_ms_cs_count
            FROM applicants
            WHERE degree = 'Masters'
              AND program ILIKE '%computer science%'
              AND (program ILIKE '%hopkins%' OR program ILIKE '%jhu%');
            """
        )
        jhu_ms_cs_count = cur.fetchone()[0]

        # Question 8
        cur.execute(
            r"""
            SELECT count(*) AS q8_original_field_count
            FROM applicants
            WHERE term = 'Fall 2026'
              AND status LIKE 'Accepted%'
              AND degree = 'PhD'
              AND program ILIKE '%computer science%'
              AND (
                program ILIKE '%georgetown%'
               OR program ILIKE '%stanford%'
               OR program ILIKE '%massachusetts institute of technology%'
               OR program ~* '\mmit\M'
               OR program ILIKE '%carnegie mellon%'
               OR program ~* '\mcmu\M'
                );
            """
        )
        original_field_count = cur.fetchone()[0]

        # Question 9
        cur.execute(
            r"""
            SELECT count(*) AS q9_llm_field_count
            FROM applicants
            WHERE term = 'Fall 2026'
              AND status LIKE 'Accepted%'
              AND degree = 'PhD'
              AND llm_generated_program ILIKE '%computer science%'
              AND (
                llm_generated_university ILIKE '%georgetown%'
               OR llm_generated_university ILIKE '%stanford%'
               OR llm_generated_university ILIKE '%massachusetts institute of technology%'
               OR llm_generated_university ~* '\mmit\M'
               OR llm_generated_university ILIKE '%carnegie mellon%'
               OR llm_generated_university ~* '\mcmu\M'
                );
            """
        )
        llm_field_count = cur.fetchone()[0]

        # Additional question 1
        # What is the acceptance percentage for applicants that have the following GPAs: 0-0.9, 1-1.9, 2.0-2.9, 3.0-3.9, 4.0-4.9?
        cur.execute(
            """
            SELECT CASE
                       WHEN gpa < 1 THEN '0-0.9'
                       WHEN gpa < 2 THEN '1-1.9'
                       WHEN gpa < 3 THEN '2.0-2.9'
                       WHEN gpa < 4 THEN '3.0-3.9'
                       ELSE '4.0-4.9'
                       END  AS gpa_bucket,
                   count(*) AS total_in_bucket,
                   ROUND(
                           100.0 * count(*) FILTER (WHERE status LIKE '%Accepted%'):: numeric
                               / NULLIF (count (*), 0),
                           2
                   )        AS acceptance_pct
            FROM applicants
            WHERE gpa IS NOT NULL
            GROUP BY 1
            ORDER BY MIN(gpa);
            """,
        )
        gpa_bucket_rows = cur.fetchall()

        # Additional question 2
        # How many applicants are there each term? Has the number been trending up or down?
        cur.execute(
            r"""
            SELECT term,
                   count(*) AS applicant_count
            FROM applicants
            WHERE term IS NOT NULL
            GROUP BY term
            ORDER BY (regexp_match(term, '\d{4}'))[1]::int,
                        CASE
                            WHEN term LIKE '%Spring%' THEN 0
                WHEN term LIKE '%Summer%' THEN 1
                WHEN term LIKE '%Fall%' THEN 2
                WHEN term LIKE '%Winter%' THEN 3
                ELSE 4
            END;
            """
        )
        term_count_rows = cur.fetchall()

    return {
        "count1": count1,
        "percent_international": percent_international,
        "avg_gpa": avg_gpa,
        "avg_gre_q": avg_gre_q,
        "avg_gre_v": avg_gre_v,
        "avg_gre_aw": avg_gre_aw,
        "avg_gpa_american_fall2026": avg_gpa_american_fall2026,
        "fall_2025_acceptance_pct": fall_2025_acceptance_pct,
        "avg_gpa_accepted_fall2026": avg_gpa_accepted_fall2026,
        "jhu_ms_cs_count": jhu_ms_cs_count,
        "original_field_count": original_field_count,
        "llm_field_count": llm_field_count,
        "gpa_bucket_rows": gpa_bucket_rows,
        "term_count_rows": term_count_rows,
    }


def term_trend(term_count_rows: list) -> str:
    """Describe the trend in applicant counts from the first to last term.

    :param term_count_rows: ``(term, count)`` rows in chronological order.
    :returns: ``"Trending up"``, ``"Trending down"`` or ``"No trend"``.
    :rtype: str

    >>> term_trend([("Fall 2024", 10), ("Fall 2025", 20)])
    'Trending up'
    """
    if not term_count_rows:
        return 'No trend'
    first_count, last_count = term_count_rows[0][1], term_count_rows[-1][1]
    if first_count > last_count:
        return 'Trending down'
    if first_count < last_count:
        return 'Trending up'
    return 'No trend'


def print_results(r: dict) -> None:
    """Print every question and its answer to stdout.

    :param r: The dict returned by :func:`run_queries`.
    :rtype: None
    """
    print('Question 1: How many entries are from applicants who applied for Fall 2026?')
    print(f"Fall 2026 applicant count: {r['count1']}")

    print('Question 2: Among entries that provide a nationality classification, what percentage are international students?')
    print(f"Percent international: {r['percent_international']}%")

    print('Question 3: What are the average GPA, GRE Quantitative, GRE Verbal, and GRE Analytical Writing scores of applicants who provide each metric?')
    print(f"Average GPA: {r['avg_gpa']}")
    print(f"Average GRE Quantitative: {r['avg_gre_q']}")
    print(f"Average GRE Verbal: {r['avg_gre_v']}")
    print(f"Average GRE Analytical Writing: {r['avg_gre_aw']}")

    print('Question 4: What is the average GPA of American applicants who applied for Fall 2026?')
    print(f"Average GPA (American, Fall 2026): {r['avg_gpa_american_fall2026']}")

    print('Question 5: What percentage of Fall 2025 entries are acceptances?')
    print(f"Fall 2025 acceptance percentage: {r['fall_2025_acceptance_pct']}%")

    print('Question 6: What is the average GPA of accepted applicants who applied for Fall 2026?')
    print(f"Average GPA (accepted, Fall 2026): {r['avg_gpa_accepted_fall2026']}")

    print('Question 7: How many entries are from applicants who applied to Johns Hopkins University for a master\'s degree in Computer Science?')
    print(f"Johns Hopkins Masters Computer Science count: {r['jhu_ms_cs_count']}")

    print('Question 8: How many Fall 2026 entries are acceptances from applicants applying for a PhD in Computer Science at one of the following universities?'
          '\nGeorgetown University, Massachusetts Institute of Technology / MIT, Stanford University, Carnegie Mellon University')
    print(f"Applicants for PhD in Computer Science at the above schools: {r['original_field_count']}")

    print('Question 9: Repeat question 8 but use the LLM generated fields.')
    print(f"Original-field count: {r['original_field_count']}")
    print(f"LLM-field count: {r['llm_field_count']}")
    print(f"Difference: +{r['original_field_count'] - r['llm_field_count']}")

    print('Additional Question 1: What is the acceptance percentage for applicants that have the following GPAs: 0-0.9, 1-1.9, 2.0-2.9, 3.0-3.9, 4.0-4.9?')
    for bucket, total_in_bucket, acceptance_pct in r['gpa_bucket_rows']:
        print(f'GPA {bucket}: {acceptance_pct}% (n={total_in_bucket})')

    print('Additional Question 2: How many applicants are there each term? Has the number been trending up or down?')
    for term, applicant_count in r['term_count_rows']:
        print(f'{term}: {applicant_count}')
    print(term_trend(r['term_count_rows']))


def main() -> None:
    """Command-line entry point: connect, run all queries, print answers.

    The connection is always closed, even if a query fails.
    """
    conn = get_connection()
    try:
        results = run_queries(conn)
    finally:
        conn.close()
    print_results(results)


if __name__ == "__main__":
    main()