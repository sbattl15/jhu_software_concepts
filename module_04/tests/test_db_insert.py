from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import orm_queries as q
import pull_data
from models import Applicant, get_session

# Every test in this module is a "db" test (database schema/inserts/
# selects) -- see pytest.ini's markers section and the "no unmarked
# test" policy.
pytestmark = pytest.mark.db

# A prefix no real Grad Cafe URL could ever have -- lets this suite find,
# and only ever touch, the rows it created itself.
TEST_URL_PREFIX = "https://test.invalid/test-db-insert/"


def _test_url(suffix: str) -> str:
    return f"{TEST_URL_PREFIX}{suffix}"


def _delete_test_rows(session) -> None:
    session.execute(delete(Applicant).where(Applicant.url.like(f"{TEST_URL_PREFIX}%")))
    session.commit()


@pytest.fixture
def db_session():
    """A real session against the configured Postgres database. Skips
    (rather than erroring) if that database isn't reachable or doesn't
    have the applicants table yet -- this suite is only meaningful
    against a real, already-set-up database, so a connection problem is
    reported as "can't run this suite," not as a failing assertion."""
    try:
        with get_session() as setup_session:
            _delete_test_rows(setup_session)
    except SQLAlchemyError as exc:
        pytest.skip(
            "Could not reach a Postgres 'applicants' table to run the "
            "database-write tests against (check PGHOST/PGPORT/PGUSER/"
            f"PGPASSWORD/PGDATABASE): {exc}"
        )

    with get_session() as session:
        yield session
        session.rollback()  # clear anything left mid-transaction before cleanup
        _delete_test_rows(session)


def _fake_cleaned_row(suffix: str, **overrides) -> dict:
    """A row shaped like what clean_new.clean_data() plus the LLM
    standardization step in pull_data.py would hand to
    _to_applicant_row() -- built by hand here instead of actually
    scraping Grad Cafe or running the local LLM."""
    row = {
        "url": _test_url(suffix),
        "program": f"Computer Science, Test University {suffix}",
        "comments": "Test comment.",
        "date_added": "2026-02-01",
        "status": "Accepted on 1 Feb",
        "term": "Fall 2026",
        "us_or_international": "American",
        "gpa": 3.9,
        "gre": 168.0,
        "gre_v": 160.0,
        "gre_aw": 4.5,
        "degree": "PhD",
        "llm-generated-program": "Computer Science",
        "llm-generated-university": f"Test University {suffix}",
    }
    row.update(overrides)
    return row


def _required_field_names() -> list[str]:
    """Every NOT NULL column on Applicant except the auto-generated
    primary key -- derived from the real model rather than hand-typed,
    so this test can't drift out of sync with the actual schema."""
    return [
        column.name
        for column in Applicant.__table__.columns
        if not column.nullable and column.name != "p_id"
    ]


class TestInsertOnPull:
    def test_table_has_none_of_this_tests_rows_before_pulling(self, db_session):
        assert q.get_applicant_by_url(db_session, _test_url("1")) is None
        assert q.get_applicant_by_url(db_session, _test_url("2")) is None

    def test_after_pull_new_rows_exist_with_required_fields_present(self, db_session):
        cleaned_rows = [_fake_cleaned_row("1"), _fake_cleaned_row("2")]
        rows = [pull_data._to_applicant_row(r) for r in cleaned_rows]
        assert all(r is not None for r in rows), "fake rows were rejected as unusable"

        inserted = pull_data._load_rows(db_session, rows)
        assert inserted == 2

        required_fields = _required_field_names()
        assert required_fields, "expected at least one NOT NULL column on Applicant"

        for suffix in ("1", "2"):
            record = q.get_applicant_by_url(db_session, _test_url(suffix))
            assert record is not None, f"row {suffix} was not inserted"
            for field in required_fields:
                assert record[field] not in (None, ""), (
                    f"required field {field!r} is empty on the inserted row"
                )


class TestIdempotency:
    def test_pulling_the_same_data_twice_does_not_duplicate_rows(self, db_session):
        cleaned_rows = [_fake_cleaned_row("dup")]
        rows = [pull_data._to_applicant_row(r) for r in cleaned_rows]

        first_pass = pull_data._load_rows(db_session, rows)
        assert first_pass == 1

        # Same url, same everything -- simulating an accidental re-pull of
        # data that's already in the database.
        second_pass = pull_data._load_rows(db_session, rows)
        assert second_pass == 0, "re-loading identical rows should insert nothing new"

        matches = (
            db_session.execute(select(Applicant).where(Applicant.url == _test_url("dup")))
            .scalars()
            .all()
        )
        assert len(matches) == 1, f"expected exactly one row for this url, found {len(matches)}"


class TestSimpleQueryFunction:
    def test_returns_a_dict_with_the_expected_keys(self, db_session):
        cleaned = _fake_cleaned_row("query", program="Physics, Query University")
        row = pull_data._to_applicant_row(cleaned)
        assert pull_data._load_rows(db_session, [row]) == 1

        record = q.get_applicant_by_url(db_session, _test_url("query"))

        assert record is not None
        assert isinstance(record, dict)
        assert set(record.keys()) == set(q.APPLICANT_DATA_FIELDS)
        assert record["program"] == "Physics, Query University"
        assert record["url"] == _test_url("query")

    def test_missing_url_returns_none_rather_than_erroring(self, db_session):
        assert q.get_applicant_by_url(db_session, _test_url("does-not-exist")) is None