Name: Shane Battles (sbattl15)
Module Info: Module 3 Assignment: Database Queries

load_data.py: This is the foundational piece of the assignment. It connects to PostgreSQL to create a database with a table called "applicants" with the columns required in the assignment. It then uses uploads the applicant data JSON into the SQL table.

query_data.py: This program accesses the SQL database and runs multiple SQL queries in order to answer 9 questions from the assignment, plus 2 questions that I came up with. The query responses are printed to the console.

models.py: This program sets up the SQLAlchemy session that is used by orm_queries.py. 

orm_queries.py: This program connects to models.py to access the SQLAlchemy session. It then runs the same queries from query_data.py through the applicants table using SQLAlchemy instead of SQL. The query responses are printed to the console.


Known bugs:
No known bugs

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