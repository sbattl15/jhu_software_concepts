Name: Shane Battles (sbattl15)
Module Info: Module 2 Assignment: Web Scraping

load_data.py: 

query_data.py:

models.py:

orm_queries.py:



Known bugs:
scrape.py: Sometimes a CAPTCHA error will pop up and end the scraping process. The program can be reran from that last page and it will continue running. My third attempt ran the full number of entries without hitting a CAPTCHA blocker so it can be a little random.

Question 5 analysis:
Raw SQL query: 
"""
SELECT ROUND(
    COUNT(*) FILTER (WHERE term = 'Fall 2025' AND status LIKE 'Accepted%%')::numeric
    * 100.0
    / NULLIF(COUNT(*) FILTER (WHERE term = 'Fall 2025'), 0),
    2
) AS fall_2025_acceptance_pct
FROM applicants;
"""

SQLAlchemy query:
accepted_fall_2025 = func.count(
        case(
            (
                and_(Applicant.term == "Fall 2025", Applicant.status.like(ACCEPTED_PATTERN)),
                1,
            )
        )
    )
    total_fall_2025 = func.count(case((Applicant.term == "Fall 2025", 1)))

    stmt = _percent(accepted_fall_2025, total_fall_2025)

Comparison: The SQLAlchemy query is written in a much more Pythonic style. It uses a function with standard Python formatting to filter and analyze the table. the raw SQL query, on the other hand uses a style that is unique and very different from Python. SQL uses the term FILTER and LIKE to find values that are equal or close to the desired filter value, while the SQLAlchemy code uses == to show equal to. Depending on the user, the option to use SQL or Python style code could be very useful.