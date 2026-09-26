from __future__ import annotations

from sqlalchemy import Integer, Numeric, and_, case, cast, func, or_, select

from models import Applicant, get_session

# --------------------------------------------------------------------------
# Shared filter values (kept as named constants, same values query_data.py
# uses, so the two scripts are easy to diff against each other).

TARGET_TERM = "Fall 2026"
ACCEPTED_PATTERN = "Accepted%"
CS_PATTERN = "%computer science%"
GEORGETOWN_PATTERN = "%georgetown%"
STANFORD_PATTERN = "%stanford%"
MIT_FULL_PATTERN = "%massachusetts institute of technology%"
MIT_ABBREV_REGEX = r"\mmit\M"
CMU_FULL_PATTERN = "%carnegie mellon%"
CMU_ABBREV_REGEX = r"\mcmu\M"
JHU_FULL_PATTERN = "%hopkins%"
JHU_ABBREV_PATTERN = "%jhu%"


# --------------------------------------------------------------------------
# Small helpers so every ROUND(..., 2) / percentage is built the same way.

def _round2(expr):
    return func.round(cast(expr, Numeric), 2)


def _percent(numerator, denominator):
    return _round2(numerator * 100.0 / func.nullif(denominator, 0))


def question_1(session) -> int:
    """How many entries are from applicants who applied for Fall 2026?"""
    stmt = (
        select(func.count())
        .select_from(Applicant)
        .where(Applicant.term == TARGET_TERM)
    )
    return session.execute(stmt).scalar_one()


def question_2(session):
    """Among entries that provide a nationality classification, what
    percentage are international students?"""
    international_count = func.count(
        case((Applicant.us_or_international == "International", 1))
    )
    classified_count = func.count(
        case(
            (
                and_(
                    Applicant.us_or_international.is_not(None),
                    func.btrim(Applicant.us_or_international) != "",
                ),
                1,
            )
        )
    )
    stmt = select(_percent(international_count, classified_count))
    return session.execute(stmt).scalar_one()


def question_3(session):
    """What are the average GPA, GRE Quantitative, GRE Verbal, and GRE
    Analytical Writing scores of applicants who provide each metric?"""
    stmt = select(
        _round2(func.avg(Applicant.gpa)).label("avg_gpa"),
        _round2(func.avg(Applicant.gre)).label("avg_gre_q"),
        _round2(func.avg(Applicant.gre_v)).label("avg_gre_v"),
        _round2(func.avg(Applicant.gre_aw)).label("avg_gre_aw"),
    )
    return session.execute(stmt).one()


def question_4(session):
    """What is the average GPA of American applicants who applied for
    Fall 2026?"""
    stmt = select(_round2(func.avg(Applicant.gpa))).where(
        and_(
            Applicant.term == TARGET_TERM,
            Applicant.us_or_international == "American",
            Applicant.gpa.is_not(None),
        )
    )
    return session.execute(stmt).scalar_one()


def question_5(session):
    """What percentage of Fall 2025 entries are acceptances?"""
    accepted_fall_2025 = func.count(
        case(
            (
                and_(Applicant.term == "Fall 2025", Applicant.status.like(ACCEPTED_PATTERN)),
                1,
            )
        )
    )
    total_fall_2025 = func.count(case((Applicant.term == "Fall 2025", 1)))

    stmt = select(_percent(accepted_fall_2025, total_fall_2025))
    return session.execute(stmt).scalar_one()


def question_6(session):
    """What is the average GPA of accepted applicants who applied for
    Fall 2025?"""
    stmt = select(_round2(func.avg(Applicant.gpa))).where(
        and_(
            Applicant.term == "Fall 2025",
            Applicant.status.like(ACCEPTED_PATTERN),
            Applicant.gpa.is_not(None),
        )
    )
    return session.execute(stmt).scalar_one()


def question_7(session) -> int:
    """How many entries are from applicants who applied to Johns Hopkins
    University for a master's degree in Computer Science?"""
    stmt = (
        select(func.count())
        .select_from(Applicant)
        .where(
            and_(
                Applicant.degree == "Masters",
                Applicant.program.ilike(CS_PATTERN),
                or_(
                    Applicant.program.ilike(JHU_FULL_PATTERN),
                    Applicant.program.ilike(JHU_ABBREV_PATTERN),
                ),
            )
        )
    )
    return session.execute(stmt).scalar_one()


def _school_match(university_col):
    """Georgetown / Stanford / MIT / Carnegie Mellon, matched the same way
    query_data.py's Q8/Q9 do: a plain substring for Georgetown/Stanford,
    and for MIT/CMU both a full-name substring AND a whole-word-boundary
    regex (~*) so bare abbreviations ("MIT", "CMU") match without also
    matching unrelated words that merely contain those letters. Q8 passes
    the raw "program" column (university name and department share that
    one free-text field); Q9 passes "llm_generated_university" instead."""
    return or_(
        university_col.ilike(GEORGETOWN_PATTERN),
        university_col.ilike(STANFORD_PATTERN),
        university_col.ilike(MIT_FULL_PATTERN),
        university_col.op("~*")(MIT_ABBREV_REGEX),
        university_col.ilike(CMU_FULL_PATTERN),
        university_col.op("~*")(CMU_ABBREV_REGEX),
    )


def question_8(session) -> int:
    """How many Fall 2026 entries are acceptances from applicants applying
    for a PhD in Computer Science at Georgetown, MIT, Stanford, or Carnegie
    Mellon -- using the original (non-LLM) program field?"""
    stmt = (
        select(func.count())
        .select_from(Applicant)
        .where(
            and_(
                Applicant.term == TARGET_TERM,
                Applicant.status.like(ACCEPTED_PATTERN),
                Applicant.degree == "PhD",
                Applicant.program.ilike(CS_PATTERN),
                _school_match(Applicant.program),
            )
        )
    )
    return session.execute(stmt).scalar_one()


def question_9(session) -> int:
    """Repeat Question 8, but identify university/program via the
    LLM-generated fields instead of the raw program text."""
    stmt = (
        select(func.count())
        .select_from(Applicant)
        .where(
            and_(
                Applicant.term == TARGET_TERM,
                Applicant.status.like(ACCEPTED_PATTERN),
                Applicant.degree == "PhD",
                Applicant.llm_generated_program.ilike(CS_PATTERN),
                _school_match(Applicant.llm_generated_university),
            )
        )
    )
    return session.execute(stmt).scalar_one()


def additional_question_1(session):
    """What is the acceptance percentage for applicants in each GPA range
    (0-0.9, 1-1.9, 2.0-2.9, 3.0-3.9, 4.0-4.9)? Returns a list of
    (label, total_in_bucket, acceptance_pct) rows, low-GPA-bucket first."""
    gpa_bucket = case(
        (Applicant.gpa < 1, "0-0.9"),
        (Applicant.gpa < 2, "1-1.9"),
        (Applicant.gpa < 3, "2.0-2.9"),
        (Applicant.gpa < 4, "3.0-3.9"),
        else_="4.0-4.9",
    ).label("gpa_bucket")

    total_in_bucket = func.count()
    accepted_in_bucket = func.count(case((Applicant.status.like(ACCEPTED_PATTERN), 1)))

    stmt = (
        select(
            gpa_bucket,
            total_in_bucket.label("total_in_bucket"),
            _percent(accepted_in_bucket, total_in_bucket).label("acceptance_pct"),
        )
        .where(Applicant.gpa.is_not(None))
        .group_by(gpa_bucket)
        .order_by(func.min(Applicant.gpa))
    )
    return session.execute(stmt).all()


def additional_question_2(session):
    """How many applicants are there each term? Returns a list of
    (term, applicant_count) rows, ordered chronologically (year, then
    Spring/Summer/Fall/Winter within a year), the same order
    query_data.py's raw-SQL version produces."""
    year_expr = cast(func.substring(Applicant.term, r"\d{4}"), Integer)
    season_rank = case(
        (Applicant.term.like("%Spring%"), 0),
        (Applicant.term.like("%Summer%"), 1),
        (Applicant.term.like("%Fall%"), 2),
        (Applicant.term.like("%Winter%"), 3),
        else_=4,
    )

    stmt = (
        select(Applicant.term, func.count().label("applicant_count"))
        .where(Applicant.term.is_not(None))
        .group_by(Applicant.term)
        .order_by(year_expr, season_rank)
    )
    return session.execute(stmt).all()


# --------------------------------------------------------------------------
# Simple lookup used by the database tests (tests/test_db_insert.py).

# Every data column on Applicant, i.e. everything except the auto-generated
# p_id primary key -- derived from the model so it can't drift out of sync.
APPLICANT_DATA_FIELDS = tuple(
    column.name for column in Applicant.__table__.columns if column.name != "p_id"
)


def get_applicant_by_url(session, url: str):
    """Returns the applicant row with this url as a plain dict (keyed by
    APPLICANT_DATA_FIELDS), or None if no row has that url."""
    stmt = select(Applicant).where(Applicant.url == url)
    applicant = session.execute(stmt).scalar_one_or_none()
    if applicant is None:
        return None
    return {field: getattr(applicant, field) for field in APPLICANT_DATA_FIELDS}


def main() -> None:
    with get_session() as session:
        count1 = question_1(session)
        percent_international = question_2(session)
        avg_gpa, avg_gre_q, avg_gre_v, avg_gre_aw = question_3(session)
        avg_gpa_american_fall2026 = question_4(session)
        fall_2025_acceptance_pct = question_5(session)
        avg_gpa_accepted_fall2025 = question_6(session)
        jhu_ms_cs_count = question_7(session)
        original_field_count = question_8(session)
        llm_field_count = question_9(session)
        gpa_bucket_rows = additional_question_1(session)
        term_count_rows = additional_question_2(session)

    print("Question 1: How many entries are from applicants who applied for Fall 2026?")
    print(f"Fall 2026 applicant count: {count1}")

    print("Question 2: Among entries that provide a nationality classification, what percentage are international students?")
    print(f"Percent international: {percent_international}%")

    print("Question 3: What are the average GPA, GRE Quantitative, GRE Verbal, and GRE Analytical Writing scores of applicants who provide each metric?")
    print(f"Average GPA: {avg_gpa}")
    print(f"Average GRE Quantitative: {avg_gre_q}")
    print(f"Average GRE Verbal: {avg_gre_v}")
    print(f"Average GRE Analytical Writing: {avg_gre_aw}")

    print("Question 4: What is the average GPA of American applicants who applied for Fall 2026?")
    print(f"Average GPA (American, Fall 2026): {avg_gpa_american_fall2026}")

    print("Question 5: What percentage of Fall 2025 entries are acceptances?")
    print(f"Fall 2025 acceptance percentage: {fall_2025_acceptance_pct}%")

    print("Question 6: What is the average GPA of accepted applicants who applied for Fall 2025?")
    print(f"Average GPA (accepted, Fall 2025): {avg_gpa_accepted_fall2025}")

    print("Question 7: How many entries are from applicants who applied to Johns Hopkins University for a master's degree in Computer Science?")
    print(f"Johns Hopkins Masters Computer Science count: {jhu_ms_cs_count}")

    print(
        "Question 8: How many Fall 2026 entries are acceptances from applicants applying "
        "for a PhD in Computer Science at one of the following universities?"
        "\nGeorgetown University, Massachusetts Institute of Technology / MIT, "
        "Stanford University, Carnegie Mellon University"
    )
    print(f"Applicants for PhD in Computer Science at the above schools: {original_field_count}")

    print("Question 9: Repeat question 8 but use the LLM generated fields.")
    print(f"Original-field count: {original_field_count}")
    print(f"LLM-field count: {llm_field_count}")
    print(f"Difference: {llm_field_count - original_field_count:+d}")

    print(
        "Additional Question 1: What is the acceptance percentage for applicants that have "
        "the following GPAs: 0-0.9, 1-1.9, 2.0-2.9, 3.0-3.9, 4.0-4.9?"
    )
    for label, total_in_bucket, acceptance_pct in gpa_bucket_rows:
        print(f"GPA {label}: {acceptance_pct}% (n={total_in_bucket})")

    print("Additional Question 2: How many applicants are there each term? Has the number been trending up or down?")
    for term, applicant_count in term_count_rows:
        print(f"{term}: {applicant_count}")
    if len(term_count_rows) >= 2:
        first_count = term_count_rows[0][1]
        last_count = term_count_rows[-1][1]
        if first_count < last_count:
            print("Trending up")
        elif first_count > last_count:
            print("Trending down")
        else:
            print("No trend")


if __name__ == "__main__":
    main()
