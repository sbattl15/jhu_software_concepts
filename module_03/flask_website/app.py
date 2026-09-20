from __future__ import annotations

from datetime import datetime

from flask import Flask, render_template

import orm_queries as q
from models import get_session

app = Flask(__name__)


@app.route("/")
def analysis() -> str:
    """Single dynamic page: runs every analysis question against Postgres
    (through the SQLAlchemy Applicant model, via orm_queries.py) and
    renders the results."""
    with get_session() as session:
        results = [
            {
                "number": 1,
                "question": "How many entries are from applicants who applied for Fall 2026?",
                "answer": f"{q.question_1(session):,}",
            },
            {
                "number": 2,
                "question": (
                    "Among entries that provide a nationality classification, "
                    "what percentage are international students?"
                ),
                "answer": f"{q.question_2(session)}%",
            },
        ]

        avg_gpa, avg_gre_q, avg_gre_v, avg_gre_aw = q.question_3(session)
        results.append(
            {
                "number": 3,
                "question": (
                    "What are the average GPA, GRE Quantitative, GRE Verbal, and GRE "
                    "Analytical Writing scores of applicants who provide each metric?"
                ),
                "answer": (
                    f"GPA: {avg_gpa} &nbsp;|&nbsp; GRE Quant: {avg_gre_q} &nbsp;|&nbsp; "
                    f"GRE Verbal: {avg_gre_v} &nbsp;|&nbsp; GRE AW: {avg_gre_aw}"
                ),
            }
        )

        results.append(
            {
                "number": 4,
                "question": "What is the average GPA of American applicants who applied for Fall 2026?",
                "answer": str(q.question_4(session)),
            }
        )
        results.append(
            {
                "number": 5,
                "question": "What percentage of Fall 2025 entries are acceptances?",
                "answer": f"{q.question_5(session)}%",
            }
        )
        results.append(
            {
                "number": 6,
                "question": "What is the average GPA of accepted applicants who applied for Fall 2025?",
                "answer": str(q.question_6(session)),
            }
        )
        results.append(
            {
                "number": 7,
                "question": (
                    "How many entries are from applicants who applied to Johns Hopkins "
                    "University for a master's degree in Computer Science?"
                ),
                "answer": f"{q.question_7(session):,}",
            }
        )
        original_field_count = q.question_8(session)
        results.append(
            {
                "number": 8,
                "question": (
                    "How many Fall 2026 entries are acceptances from applicants applying for a "
                    "PhD in Computer Science at Georgetown, MIT, Stanford, or Carnegie Mellon?"
                ),
                "answer": f"{original_field_count:,}",
            }
        )

        llm_field_count = q.question_9(session)
        diff = llm_field_count - original_field_count
        results.append(
            {
                "number": 9,
                "question": "Repeat Question 8, but identify university/program using the LLM-generated fields.",
                "answer": (
                    f"{llm_field_count:,} "
                    f"(original-field count was {original_field_count:,}, "
                    f"a difference of {diff:+,})"
                ),
            }
        )

        # -- Original question 1: acceptance rate by GPA bucket ------------
        gpa_bucket_rows = q.additional_question_1(session)
        gpa_buckets = [
            {
                "label": label,
                "total": total_in_bucket,
                "pct": float(acceptance_pct) if acceptance_pct is not None else 0.0,
            }
            for label, total_in_bucket, acceptance_pct in gpa_bucket_rows
        ]

        # -- Original question 2: applicant volume by term, with trend -----
        term_count_rows = q.additional_question_2(session)
        term_counts = [{"term": term, "count": count} for term, count in term_count_rows]
        max_term_count = max((row["count"] for row in term_counts), default=0)
        for row in term_counts:
            row["pct_of_max"] = (row["count"] / max_term_count * 100) if max_term_count else 0

        trend = "no data"
        if len(term_count_rows) >= 2:
            first_count, last_count = term_count_rows[0][1], term_count_rows[-1][1]
            if last_count > first_count:
                trend = "trending up"
            elif last_count < first_count:
                trend = "trending down"
            else:
                trend = "no clear trend"

    return render_template(
        "index.html",
        results=results,
        gpa_buckets=gpa_buckets,
        term_counts=term_counts,
        trend=trend,
        generated_at=datetime.now().strftime("%B %d, %Y at %I:%M %p"),
    )


if __name__ == "__main__":
    app.run(debug=True)
