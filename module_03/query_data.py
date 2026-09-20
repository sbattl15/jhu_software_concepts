import os
import psycopg

conn = psycopg.connect(
    dbname=os.environ.get("PGDATABASE", "gradcafe_applications"),
    user=os.environ.get("PGUSER", "postgres"),
    password=os.environ.get("PGPASSWORD"),
    host=os.environ.get("PGHOST", "localhost"),
    port=os.environ.get("PGPORT", "5432"),
)

try:
    with conn.cursor() as cur:
        # Qeustion 1
        cur.execute("SELECT count(*) FROM applicants WHERE term = %s;", ("Fall 2026",))
        count1 = cur.fetchone()[0]

        # Question 2
        cur.execute(
            """
            SELECT ROUND(
                           COUNT(*) FILTER (WHERE us_or_international = 'International')::numeric
                        * 100.0
                        / NULLIF(
                            COUNT(*) FILTER (
                                WHERE us_or_international IS NOT NULL
                                  AND btrim(us_or_international) <> ''
                            ),
                            0
                        ),
                           2
                   ) AS percent_international
            FROM applicants;
            """
        )
        percent_international = cur.fetchone()[0]

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
            WHERE term = %s
              AND us_or_international = 'American'
              AND gpa IS NOT NULL;
            """,
            ("Fall 2026",),
        )
        avg_gpa_american_fall2026 = cur.fetchone()[0]

        # Question 5
        cur.execute(
            """
            SELECT ROUND(
                COUNT(*) FILTER (WHERE term = %(term)s AND status LIKE 'Accepted%%')::numeric
                * 100.0
                / NULLIF(COUNT(*) FILTER (WHERE term = %(term)s), 0),
                2
            ) AS fall_2025_acceptance_pct
            FROM applicants;
            """,
            {"term": "Fall 2025"},
        )
        fall_2025_acceptance_pct = cur.fetchone()[0]

        # Question 6
        cur.execute(
            """
            SELECT ROUND(AVG(gpa)::numeric, 2) AS avg_gpa_accepted_fall2026
            FROM applicants
            WHERE term = %s
              AND status LIKE 'Accepted%%'
              AND gpa IS NOT NULL;
            """,
            ("Fall 2026",),
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
            """
            SELECT count(*) AS q8_original_field_count
            FROM applicants
            WHERE term = %(term)s
              AND status LIKE %(accepted_pattern)s
              AND degree = %(degree)s
              AND program ILIKE %(cs_pattern)s
              AND (
                program ILIKE %(georgetown_pattern)s
               OR program ILIKE %(stanford_pattern)s
               OR program ILIKE %(mit_full_pattern)s
               OR program ~* %(mit_abbrev_regex)s
               OR program ILIKE %(cmu_full_pattern)s
               OR program ~* %(cmu_abbrev_regex)s
                );
            """,
            {
                "term": "Fall 2026",
                "accepted_pattern": "Accepted%",
                "degree": "PhD",
                "cs_pattern": "%computer science%",
                "georgetown_pattern": "%georgetown%",
                "stanford_pattern": "%stanford%",
                "mit_full_pattern": "%massachusetts institute of technology%",
                "mit_abbrev_regex": r"\mmit\M",
                "cmu_full_pattern": "%carnegie mellon%",
                "cmu_abbrev_regex": r"\mcmu\M",
            },
        )
        original_field_count = cur.fetchone()[0]

        # Question 9
        cur.execute(
            """
            SELECT count(*) AS q9_llm_field_count
            FROM applicants
            WHERE term = %(term)s
              AND status LIKE %(accepted_pattern)s
              AND degree = %(degree)s
              AND llm_generated_program ILIKE %(cs_pattern)s
              AND (
                llm_generated_university ILIKE %(georgetown_pattern)s
               OR llm_generated_university ILIKE %(stanford_pattern)s
               OR llm_generated_university ILIKE %(mit_full_pattern)s
               OR llm_generated_university ~* %(mit_abbrev_regex)s
               OR llm_generated_university ILIKE %(cmu_full_pattern)s
               OR llm_generated_university ~* %(cmu_abbrev_regex)s
                );
            """,
            {
                "term": "Fall 2026",
                "accepted_pattern": "Accepted%",
                "degree": "PhD",
                "cs_pattern": "%computer science%",
                "georgetown_pattern": "%georgetown%",
                "stanford_pattern": "%stanford%",
                "mit_full_pattern": "%massachusetts institute of technology%",
                "mit_abbrev_regex": r"\mmit\M",
                "cmu_full_pattern": "%carnegie mellon%",
                "cmu_abbrev_regex": r"\mcmu\M",
            },
        )
        llm_field_count = cur.fetchone()[0]

        # Additional question 1
        # What is the acceptance percentage for applicants that have the following GPAs: 0-0.9, 1-1.9, 2.0-2.9, 3.0-3.9, 4.0-4.9?
        cur.execute(
            """
            SELECT FLOOR(gpa)::int AS gpa_bucket, count(*) AS total_in_bucket,
                   ROUND(
                           100.0 * count(*) FILTER (WHERE status LIKE %(accepted_pattern)s):: numeric
                               / NULLIF (count (*), 0),
                           2
                   ) AS acceptance_pct
            FROM applicants
            WHERE gpa IS NOT NULL
            GROUP BY FLOOR(gpa)
            ORDER BY FLOOR(gpa);
            """,
            {"accepted_pattern": "Accepted%"},
        )
        gpa_bucket_rows = cur.fetchall()
        gpa_bucket_labels = {
            0: '0-0.9',
            1: '1-1.9',
            2: '2.0-2.9',
            3: '3.0-3.9',
            4: '4.0-4.9',
        }

        # Additional question 2
        # How many applicants are there each term? Has the number been trending up or down?
        cur.execute(
            """
            SELECT term,
                   count(*) AS applicant_count
            FROM applicants
            WHERE term IS NOT NULL
            GROUP BY term
            ORDER BY (regexp_match(term, %(year_regex)s))[1]::int,
                        CASE
                            WHEN term LIKE %(spring_pattern)s THEN 0
                WHEN term LIKE %(summer_pattern)s THEN 1
                WHEN term LIKE %(fall_pattern)s THEN 2
                WHEN term LIKE %(winter_pattern)s THEN 3
                ELSE 4
            END;
            """,
            {
                "year_regex": r"\d{4}",
                "spring_pattern": "Spring%",
                "summer_pattern": "Summer%",
                "fall_pattern": "Fall%",
                "winter_pattern": "Winter%",
            },
        )
        term_count_rows = cur.fetchall()

finally:
    conn.close()


print('Question 1: How many entries are from applicants who applied for Fall 2026?')
print(f"Fall 2026 applicant count: {count1}")
print('Question 2: Among entries that provide a nationality classification, what percentage are international students?')
print(f'Percent international: {percent_international}%')
print('Question 3: What are the average GPA, GRE Quantitative, GRE Verbal, and GRE Analytical Writing scores of applicants who provide each metric?')
print(f'Average GPA: {avg_gpa}')
print(f'Average GRE Quantitative: {avg_gre_q}')
print(f'Average GRE Verbal: {avg_gre_v}')
print(f'Average GRE Analytical Writing: {avg_gre_aw}')

print('Question 4: What is the average GPA of American applicants who applied for Fall 2026?')
print(f'Average GPA (American, Fall 2026): {avg_gpa_american_fall2026}')

print('Question 5: What percentage of Fall 2025 entries are acceptances?')
print(f'Fall 2025 acceptance percentage: {fall_2025_acceptance_pct}%')

print('Question 6: What is the average GPA of accepted applicants who applied for Fall 2026?')
print(f'Average GPA (accepted, Fall 2026): {avg_gpa_accepted_fall2026}')

print('Question 7: How many entries are from applicants who applied to Johns Hopkins University for a master\'s degree in Computer Science?')
print(f'Johns Hopkins Masters Computer Science count: {jhu_ms_cs_count}')

print('Question 8: How many Fall 2026 entries are acceptances from applicants applying for a PhD in Computer Science at one of the following universities?'
      '\n Georgetown University, Massachusetts Institute of Technology / MIT, Stanford University, Carnegie Mellon University')
print(f'Applicants for PhD in Computer Science at the above schools: {original_field_count}')

print('Question 9: Repeat question 8 but use the LLM generated fields.')
print(f'Original-field count: {original_field_count}')
print(f'LLM-field count: {llm_field_count}')
print(f'Difference: +{original_field_count - llm_field_count}')

print('What is the acceptance percentage for applicants that have the following GPAs: 0-0.9, 1-1.9, 2.0-2.9, 3.0-3.9, 4.0-4.9?')
for bucket, total_in_bucket, acceptance_pct in gpa_bucket_rows:
    label = gpa_bucket_labels.get(bucket, f'{bucket}.0-{bucket}.9')
    print(f'GPA {label} acceptance percentage: {acceptance_pct}% (n={total_in_bucket})')


print('How many applicants are there each term? Has the number been trending up or down?')
for term, applicant_count in term_count_rows:
    print(f'{term}: {applicant_count}')
if term_count_rows[0] > term_count_rows[-1]:
    print('Trending down')
elif term_count_rows[0] < term_count_rows[-1]:
    print('Trending up')
else:
    print('No trend')