"""Unit tests for pull_data.py -- the scrape -> clean -> LLM -> load
pipeline the "Pull Data" button runs. The scraper, cleaner, LLM and
database are all faked, so nothing touches the network or Postgres."""

from __future__ import annotations

import json
import runpy
from datetime import date

import pytest

import clean
import llm_standardize
import models
import pull_data
import scrape

pytestmark = pytest.mark.buttons


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Stands in for a SQLAlchemy session; the INSERT ... RETURNING reports
    `inserted` new p_ids (the rest are treated as url conflicts)."""

    def __init__(self, inserted=1, fail=False):
        self.inserted = inserted
        self.fail = fail
        self.committed = False
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt):
        if self.fail:
            raise RuntimeError("database is down")
        self.statements.append(stmt)
        return _Result([(i,) for i in range(self.inserted)])

    def commit(self):
        self.committed = True


def _row(**overrides):
    row = {
        "url": "https://www.thegradcafe.com/result/1",
        "program": "Computer Science, MIT",
        "comments": "hi",
        "date_added": "2026-02-01",
        "status": "Accepted",
        "term": "Fall 2026",
        "us_or_international": "American",
        "gpa": 3.9,
        "gre": 168.0,
        "gre_v": 160.0,
        "gre_aw": 4.5,
        "degree": "phd",
        "llm-generated-program": "Computer Science",
        "llm-generated-university": "Massachusetts Institute of Technology",
    }
    row.update(overrides)
    return row


class TestNormalizeDegree:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, None),
            ("", None),
            ("Master’s", "Masters"),
            (" PhD ", "PhD"),
            ("JD", "JD"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert pull_data._normalize_degree(raw) == expected


class TestStandardizeWithLlm:
    def test_calls_llm_once_per_distinct_program_and_survives_failures(
        self, monkeypatch, capsys
    ):
        calls = []

        def fake_call_llm(text):
            calls.append(text)
            if text == "bad":
                raise RuntimeError("model crashed")
            return {"standardized_program": f"P:{text}", "standardized_university": "U"}

        monkeypatch.setattr(llm_standardize, "_call_llm", fake_call_llm)

        # 50 distinct strings exercises the every-50 progress line; plus a
        # duplicate, a failing string and a row with no program at all.
        rows = [{"program": f"prog {i}"} for i in range(50)]
        rows += [{"program": "prog 0"}, {"program": "bad"}, {}]
        pull_data._standardize_with_llm(rows)

        assert len(calls) == 51  # 50 distinct + "bad"; duplicate/empty not re-run
        assert rows[0]["llm-generated-program"] == "P:prog 0"
        assert rows[50]["llm-generated-program"] == "P:prog 0"
        assert rows[51]["llm-generated-program"] == ""  # failed -> empty result
        assert rows[52]["llm-generated-university"] == ""
        captured = capsys.readouterr()
        assert "50/51 distinct strings done" in captured.out
        assert "LLM standardization failed" in captured.err

    def test_no_programs_means_no_llm_calls(self, monkeypatch):
        def _never(text):
            raise AssertionError("LLM should not be called")

        monkeypatch.setattr(llm_standardize, "_call_llm", _never)
        rows = [{"program": ""}]
        pull_data._standardize_with_llm(rows)
        assert rows[0]["llm-generated-program"] == ""


class TestToApplicantRow:
    @pytest.mark.parametrize("missing", ["url", "program"])
    def test_missing_required_field_is_rejected(self, missing):
        assert pull_data._to_applicant_row(_row(**{missing: "  "})) is None

    def test_good_row_is_mapped_onto_columns(self):
        out = pull_data._to_applicant_row(_row())
        assert out["date_added"] == date(2026, 2, 1)
        assert out["degree"] == "PhD"
        assert out["llm_generated_university"] == "Massachusetts Institute of Technology"
        assert set(out) == {c.name for c in models.Applicant.__table__.columns} - {"p_id"}

    @pytest.mark.parametrize("bad_date", ["not a date", None])
    def test_bad_or_missing_date_becomes_none(self, bad_date):
        assert pull_data._to_applicant_row(_row(date_added=bad_date))["date_added"] is None


class TestLoadRows:
    def test_empty_list_inserts_nothing(self):
        session = _FakeSession()
        assert pull_data._load_rows(session, []) == 0
        assert session.statements == []

    def test_uses_on_conflict_do_nothing_and_counts_new_rows(self):
        from sqlalchemy.dialects import postgresql

        session = _FakeSession(inserted=1)
        rows = [
            pull_data._to_applicant_row(_row(url="a")),
            pull_data._to_applicant_row(_row(url="b")),
        ]
        assert pull_data._load_rows(session, rows) == 1
        assert session.committed
        sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (url) DO NOTHING" in sql
        assert "RETURNING applicants.p_id" in sql


def _patch_pipeline(monkeypatch, *, scrape_result=None, clean_result=None, session=None):
    raw = scrape_result if scrape_result is not None else [{"entry_id": "1"}, {"entry_id": "2"}]

    def fake_scrape(max_pages):
        if isinstance(raw, Exception):
            raise raw
        return raw

    def fake_clean(entries):
        if isinstance(clean_result, Exception):
            raise clean_result
        return clean_result if clean_result is not None else [
            {"url": "u1", "program": "CS, MIT"},
            {"url": "", "program": "no url"},
        ]

    monkeypatch.setattr(scrape, "scrape_data", fake_scrape)
    monkeypatch.setattr(pull_data.scrape, "scrape_data", fake_scrape)
    monkeypatch.setattr(clean, "clean_data", fake_clean)
    monkeypatch.setattr(pull_data, "clean_data", fake_clean)
    monkeypatch.setattr(
        llm_standardize,
        "_call_llm",
        lambda text: {"standardized_program": "CS", "standardized_university": "MIT"},
    )
    fake_session = session or _FakeSession()
    monkeypatch.setattr(pull_data, "get_session", lambda: fake_session)
    monkeypatch.setattr(models, "get_session", lambda: fake_session)
    return fake_session


class TestMain:
    def test_success_prints_summary_line(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch)
        assert pull_data.main() == 0
        out = capsys.readouterr().out
        summary_line = [ln for ln in out.splitlines() if ln.startswith("PULL_DATA_SUMMARY::")]
        assert json.loads(summary_line[0].split("::", 1)[1]) == {
            "raw": 2,
            "usable": 1,
            "inserted": 1,
            "duplicates": 0,
        }
        assert "1 skipped" in out

    def test_scrape_failure_returns_1(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch, scrape_result=RuntimeError("offline"))
        assert pull_data.main() == 1
        assert "Scrape failed: offline" in capsys.readouterr().err

    def test_clean_failure_returns_1(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch, clean_result=ValueError("bad data"))
        assert pull_data.main() == 1
        assert "Cleaning failed: bad data" in capsys.readouterr().err

    def test_llm_step_failure_is_not_fatal(self, monkeypatch, capsys):
        _patch_pipeline(monkeypatch)

        def boom(rows):
            raise RuntimeError("no model")

        monkeypatch.setattr(pull_data, "_standardize_with_llm", boom)
        assert pull_data.main() == 0
        assert "continuing without it: no model" in capsys.readouterr().err

    def test_database_failure_returns_1(self, monkeypatch, capsys):
        failing_session = _FakeSession(fail=True)
        _patch_pipeline(monkeypatch, session=failing_session)
        assert pull_data.main() == 1
        assert "Database load failed: database is down" in capsys.readouterr().err
        # The failed load never commits, so no partial batch is written.
        assert failing_session.committed is False

    def test_module_main_block_exits_with_mains_return_code(self, monkeypatch):
        _patch_pipeline(monkeypatch, scrape_result=RuntimeError("offline"))
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(pull_data.__file__, run_name="__main__")
        assert exc_info.value.code == 1
