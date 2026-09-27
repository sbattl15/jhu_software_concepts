"""Unit tests for load_data.py -- the one-time bulk loader.

psycopg.connect is replaced with an in-memory fake, so these tests never
open a real database connection and run in milliseconds.
"""

from __future__ import annotations

import json
import runpy
from datetime import date

import psycopg
import pytest

import load_data

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.conn.executed.append((query, params))

    def executemany(self, query, rows):
        if self.conn.fail_on_insert:
            raise RuntimeError("insert failed")
        self.conn.inserted_rows.extend(rows)

    def fetchone(self):
        return self.conn.fetchone_value


class _FakeConnection:
    """Records every call so tests can assert on commit/rollback/close."""

    def __init__(self, fetchone_value=None, fail_on_insert=False):
        self.fetchone_value = fetchone_value
        self.fail_on_insert = fail_on_insert
        self.executed = []
        self.inserted_rows = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _ConnectRecorder:
    """What the fake psycopg.connect saw: queued connections, calls, last."""

    def __init__(self):
        self.connections = []  # connections to hand out, in order
        self.calls = []  # (args, kwargs) for every connect() call
        self.last = None  # the connection most recently handed out


@pytest.fixture
def fake_connect(monkeypatch):
    """Replaces psycopg.connect with a recorder-backed fake."""
    recorder = _ConnectRecorder()

    def _connect(*args, **kwargs):
        recorder.calls.append((args, kwargs))
        conn = recorder.connections.pop(0) if recorder.connections else _FakeConnection()
        recorder.last = conn
        return conn

    monkeypatch.setattr(psycopg, "connect", _connect)
    return recorder


SAMPLE_RECORD = {
    "program": "Computer Science, MIT",
    "comments": "  Great program  ",
    "date_added": "Added on Feb 17, 2026",
    "url": "https://www.thegradcafe.com/result/1",
    "status": "Accepted on 1 Feb",
    "term": "Fall 2026",
    "US/International": "International",
    "GPA": "GPA 3.85",
    "GRE": "GRE 166",
    "GRE V": "GRE V 160",
    "GRE AW": "GRE AW 4.5",
    "Degree": "PhD",
    "llm-generated-program": "Computer Science",
    "llm-generated-university": "Massachusetts Institute of Technology",
}


@pytest.fixture
def json_file(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps([SAMPLE_RECORD]), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


class TestParsers:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Added on Feb 17, 2026", date(2026, 2, 17)),
            ("Added on Feb 31, 2026", None),  # matches the prefix, invalid date
            ("Feb 17, 2026", None),  # missing "Added on"
            ("", None),
            (None, None),
        ],
    )
    def test_parse_date_added(self, value, expected):
        assert load_data.parse_date_added(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("GPA 3.85", 3.85),
            ("GPA 4.9", 4.9),  # weighted scale is kept, not clamped
            ("GPA 3.8.5", None),  # matches the pattern but isn't a float
            ("3.85", None),
            ("", None),
            (None, None),
        ],
    )
    def test_parse_gpa(self, value, expected):
        assert load_data.parse_gpa(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("GRE 166", 166.0),
            ("GRE V 160", 160.0),
            ("GRE AW 4.5", 4.5),
            ("GRE", None),
            ("", None),
            (None, None),
        ],
    )
    def test_parse_score(self, value, expected):
        assert load_data.parse_score(value) == expected

    @pytest.mark.parametrize(
        "value,expected", [("  hi  ", "hi"), ("   ", None), ("", None), (None, None)]
    )
    def test_clean_text(self, value, expected):
        assert load_data.clean_text(value) == expected

    def test_libpq_url_drops_the_sqlalchemy_driver(self):
        assert load_data.libpq_url("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
        assert load_data.libpq_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


class TestRecords:
    def test_load_records_reads_the_json_list(self, json_file):
        assert load_data.load_records(json_file) == [SAMPLE_RECORD]

    def test_to_row_matches_the_insert_column_order(self):
        row = load_data.to_row(SAMPLE_RECORD)
        assert row == (
            "Computer Science, MIT",
            "Great program",
            date(2026, 2, 17),
            "https://www.thegradcafe.com/result/1",
            "Accepted on 1 Feb",
            "Fall 2026",
            "International",
            3.85,
            166.0,
            160.0,
            4.5,
            "PhD",
            "Computer Science",
            "Massachusetts Institute of Technology",
        )
        assert load_data.INSERT_SQL.count("%s") == len(row)


# ---------------------------------------------------------------------------
# create_database_if_missing
# ---------------------------------------------------------------------------


class TestCreateDatabase:
    def test_creates_the_database_when_missing(self, fake_connect, capsys):
        fake_connect.connections.append(_FakeConnection(fetchone_value=None))

        load_data.create_database_if_missing("newdb", "me", "pw", "localhost", "5432")

        assert fake_connect.calls[0][1]["dbname"] == "postgres"
        assert fake_connect.calls[0][1]["autocommit"] is True
        assert len(fake_connect.last.executed) == 2  # existence check + CREATE
        assert "Created database 'newdb'" in capsys.readouterr().out

    def test_does_nothing_when_it_exists(self, fake_connect, capsys):
        fake_connect.connections.append(_FakeConnection(fetchone_value=(1,)))

        load_data.create_database_if_missing("olddb", "me", None, "localhost", "5432")

        assert len(fake_connect.last.executed) == 1  # only the existence check
        assert "already exists" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_loads_every_row_with_database_url(self, fake_connect, json_file, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "load_data.py",
                "--json-file",
                str(json_file),
                "--database-url",
                "postgresql+psycopg://u:p@h:5432/db",
            ],
        )

        load_data.main()

        conn = fake_connect.last
        assert fake_connect.calls[-1][0] == ("postgresql://u:p@h:5432/db",)
        assert conn.executed[0][0] == load_data.CREATE_TABLE_SQL
        assert conn.executed[1][0] == load_data.MIGRATE_SQL
        assert conn.inserted_rows == [load_data.to_row(SAMPLE_RECORD)]
        assert conn.committed and conn.closed and not conn.rolled_back
        assert "Successfully uploaded 1 rows" in capsys.readouterr().out

    def test_uses_pg_options_without_database_url(self, fake_connect, json_file, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(
            "sys.argv",
            ["load_data.py", "--json-file", str(json_file), "--dbname", "grad", "--host", "dbhost"],
        )

        load_data.main()

        kwargs = fake_connect.calls[-1][1]
        assert kwargs["dbname"] == "grad"
        assert kwargs["host"] == "dbhost"

    def test_create_db_flag_creates_the_database_first(self, fake_connect, json_file, monkeypatch):
        created = []
        monkeypatch.setattr(load_data, "create_database_if_missing", lambda *a: created.append(a))
        monkeypatch.setattr(
            "sys.argv",
            [
                "load_data.py",
                "--json-file",
                str(json_file),
                "--database-url",
                "postgresql://u@h/db",
                "--create-db",
            ],
        )

        load_data.main()

        assert created, "--create-db did not call create_database_if_missing()"

    def test_insert_error_rolls_back_and_reraises(self, fake_connect, json_file, monkeypatch, capsys):
        fake_connect.connections.append(_FakeConnection(fail_on_insert=True))
        monkeypatch.setattr(
            "sys.argv",
            ["load_data.py", "--json-file", str(json_file), "--database-url", "postgresql://u@h/db"],
        )

        with pytest.raises(RuntimeError, match="insert failed"):
            load_data.main()

        conn = fake_connect.last
        assert conn.rolled_back and conn.closed and not conn.committed
        assert "An error occurred: insert failed" in capsys.readouterr().out

    def test_module_main_block(self, fake_connect, json_file, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["load_data.py", "--json-file", str(json_file), "--database-url", "postgresql://u@h/db"],
        )
        runpy.run_path(load_data.__file__, run_name="__main__")
        assert fake_connect.last.committed
