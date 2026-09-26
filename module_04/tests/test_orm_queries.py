"""Unit tests for orm_queries.py that don't need a live database: every
query is compiled against the real PostgreSQL dialect (so a malformed
statement still fails here) and a fake session hands back canned rows.
tests/test_db_insert.py covers the same module against a real Postgres."""

from __future__ import annotations

import runpy

import pytest
from sqlalchemy.dialects import postgresql

import models
import orm_queries as q

pytestmark = pytest.mark.db


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def one(self):
        return self._value

    def all(self):
        return self._value


class _FakeSession:
    """Compiles every statement it's handed with the postgresql dialect and
    returns the next canned value from `values`."""

    def __init__(self, values):
        self._values = list(values)
        self.sql: list[str] = []

    def execute(self, stmt):
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
        return _Result(self._values.pop(0))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(
    "func,value,expected_sql",
    [
        (q.question_1, 10, "applicants.term ="),
        (q.question_2, 40.5, "btrim"),
        (q.question_3, (3.5, 160.0, 155.0, 4.0), "avg(applicants.gpa)"),
        (q.question_4, 3.6, "applicants.us_or_international ="),
        (q.question_5, 25.0, "nullif"),
        (q.question_6, 3.7, "applicants.status LIKE"),
        (q.question_7, 3, "applicants.degree ="),
        (q.question_8, 4, "~*"),
        (q.question_9, 5, "applicants.llm_generated_university"),
        (q.additional_question_1, [("3.0-3.9", 5, 40.0)], "GROUP BY"),
        (q.additional_question_2, [("Fall 2026", 5)], "ORDER BY"),
    ],
)
def test_each_question_builds_sql_and_returns_the_result(func, value, expected_sql):
    session = _FakeSession([value])
    assert func(session) == value
    assert expected_sql in session.sql[0]


class _FakeApplicant:
    def __init__(self, **fields):
        for name in q.APPLICANT_DATA_FIELDS:
            setattr(self, name, fields.get(name))


class TestGetApplicantByUrl:
    def test_applicant_data_fields_are_every_column_but_the_primary_key(self):
        columns = {c.name for c in models.Applicant.__table__.columns}
        assert set(q.APPLICANT_DATA_FIELDS) == columns - {"p_id"}

    def test_found_row_is_returned_as_a_dict(self):
        session = _FakeSession([_FakeApplicant(url="u", program="CS, MIT")])
        record = q.get_applicant_by_url(session, "u")
        assert set(record) == set(q.APPLICANT_DATA_FIELDS)
        assert record["url"] == "u"
        assert record["program"] == "CS, MIT"
        assert "applicants.url =" in session.sql[0]

    def test_missing_row_returns_none(self):
        assert q.get_applicant_by_url(_FakeSession([None]), "nope") is None


def _main_values(first_term_count, last_term_count):
    return [
        10,
        40.0,
        (3.5, 160.0, 155.0, 4.0),
        3.6,
        25.0,
        3.7,
        3,
        4,
        5,
        [("3.0-3.9", 5, 40.0)],
        [("Fall 2025", first_term_count), ("Fall 2026", last_term_count)],
    ]


class TestMain:
    @pytest.mark.parametrize(
        "first,last,expected",
        [(1, 2, "Trending up"), (2, 1, "Trending down"), (1, 1, "No trend")],
    )
    def test_main_prints_every_answer_and_the_trend(
        self, monkeypatch, capsys, first, last, expected
    ):
        monkeypatch.setattr(q, "get_session", lambda: _FakeSession(_main_values(first, last)))
        q.main()
        out = capsys.readouterr().out
        assert "Fall 2026 applicant count: 10" in out
        assert "Difference: +1" in out
        assert "GPA 3.0-3.9: 40.0% (n=5)" in out
        assert expected in out

    def test_main_with_a_single_term_prints_no_trend_line(self, monkeypatch, capsys):
        values = _main_values(1, 1)
        values[-1] = [("Fall 2026", 5)]
        monkeypatch.setattr(q, "get_session", lambda: _FakeSession(values))
        q.main()
        out = capsys.readouterr().out
        assert "Trending" not in out and "No trend" not in out

    def test_module_main_block(self, monkeypatch, capsys):
        monkeypatch.setattr(models, "get_session", lambda: _FakeSession(_main_values(1, 2)))
        runpy.run_path(q.__file__, run_name="__main__")
        assert "Trending up" in capsys.readouterr().out
