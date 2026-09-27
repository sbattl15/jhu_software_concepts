"""Unit tests for query_data.py -- the raw-SQL analysis script.

psycopg.connect is replaced with a fake whose cursor hands back canned
rows, so no database is needed.
"""

from __future__ import annotations

import runpy

import psycopg
import pytest

import query_data

pytestmark = pytest.mark.db


# Canned results, in the order run_queries() calls fetchone()/fetchall().
FETCHONE_RESULTS = [
    (100,),  # Q1 Fall 2026 count
    (25, 200),  # Q2 international, classified
    (3.75, 165.0, 158.0, 4.25),  # Q3 averages
    (3.8,),  # Q4
    (40.0,),  # Q5
    (3.9,),  # Q6
    (7,),  # Q7
    (12,),  # Q8
    (10,),  # Q9
]
FETCHALL_RESULTS = [
    [("3.0-3.9", 50, 45.5)],  # additional question 1
    [("Fall 2025", 10), ("Fall 2026", 30)],  # additional question 2
]


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        if self.conn.fail:
            raise RuntimeError("query failed")
        self.conn.queries.append(query)

    def fetchone(self):
        return self.conn.fetchone_results.pop(0)

    def fetchall(self):
        return self.conn.fetchall_results.pop(0)


class _FakeConnection:
    def __init__(self, fail=False):
        self.fail = fail
        self.queries = []
        self.fetchone_results = list(FETCHONE_RESULTS)
        self.fetchall_results = list(FETCHALL_RESULTS)
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_connect(monkeypatch):
    calls = []
    state = {"conn": _FakeConnection()}

    def _connect(*args, **kwargs):
        calls.append((args, kwargs))
        return state["conn"]

    monkeypatch.setattr(psycopg, "connect", _connect)
    return state, calls


class TestConnection:
    def test_uses_database_url_when_set(self, fake_connect, monkeypatch):
        _, calls = fake_connect
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
        query_data.get_connection()
        assert calls[-1][0] == ("postgresql://u:p@h:5432/db",)

    def test_falls_back_to_pg_variables(self, fake_connect, monkeypatch):
        _, calls = fake_connect
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("PGDATABASE", "grad")
        monkeypatch.setenv("PGHOST", "dbhost")
        query_data.get_connection()
        kwargs = calls[-1][1]
        assert kwargs["dbname"] == "grad"
        assert kwargs["host"] == "dbhost"

    def test_libpq_url(self):
        assert query_data.libpq_url("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"


class TestRunQueries:
    def test_returns_every_answer(self):
        conn = _FakeConnection()
        results = query_data.run_queries(conn)

        assert len(conn.queries) == 11
        assert results["count1"] == 100
        assert results["percent_international"] == 12.5
        assert (results["avg_gpa"], results["avg_gre_aw"]) == (3.75, 4.25)
        assert results["jhu_ms_cs_count"] == 7
        assert (results["original_field_count"], results["llm_field_count"]) == (12, 10)
        assert results["gpa_bucket_rows"] == FETCHALL_RESULTS[0]
        assert results["term_count_rows"] == FETCHALL_RESULTS[1]


class TestTermTrend:
    @pytest.mark.parametrize(
        "rows,expected",
        [
            ([("Fall 2025", 10), ("Fall 2026", 30)], "Trending up"),
            ([("Fall 2025", 30), ("Fall 2026", 10)], "Trending down"),
            ([("Fall 2025", 10), ("Fall 2026", 10)], "No trend"),
            ([], "No trend"),
        ],
    )
    def test_trend(self, rows, expected):
        assert query_data.term_trend(rows) == expected


class TestMain:
    def test_prints_every_question_and_closes_the_connection(self, fake_connect, capsys):
        state, _ = fake_connect
        query_data.main()
        out = capsys.readouterr().out
        assert "Question 1:" in out and "Fall 2026 applicant count: 100" in out
        assert "Percent international: 12.5%" in out
        assert "GPA 3.0-3.9: 45.5% (n=50)" in out
        assert "Fall 2026: 30" in out
        assert "Trending up" in out
        assert state["conn"].closed

    def test_connection_is_closed_when_a_query_fails(self, fake_connect):
        state, _ = fake_connect
        state["conn"] = _FakeConnection(fail=True)
        with pytest.raises(RuntimeError, match="query failed"):
            query_data.main()
        assert state["conn"].closed

    def test_module_main_block(self, fake_connect, capsys):
        runpy.run_path(query_data.__file__, run_name="__main__")
        assert "Question 9:" in capsys.readouterr().out
