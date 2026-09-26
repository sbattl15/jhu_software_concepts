"""Covers the remaining branches of app.py and models.py: the Pull Data
status watcher's success/failure paths, start_pull_data()'s error paths,
the analysis trend labels, and each module's ``__main__`` block. No
database or real subprocess is used."""

from __future__ import annotations

import runpy
from datetime import datetime

import flask
import pytest
import sqlalchemy.orm

import app as flask_app_module
import models

pytestmark = pytest.mark.buttons

IDLE_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "success": None,
    "message": "No data pull has been run yet.",
}


class _FakeSessionCtx:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(list(lines))
        self._returncode = returncode

    def wait(self):
        return self._returncode


class _ExplodingProc:
    """stdout blows up mid-read -- the watcher itself failing."""

    @property
    def stdout(self):
        def _gen():
            yield "first line"
            raise OSError("pipe broke")

        return _gen()

    def wait(self):  # never reached: stdout raises first
        return 0


@pytest.fixture(autouse=True)
def idle_state():
    with flask_app_module._pull_lock:
        flask_app_module._pull_state.update(IDLE_STATE)
    yield
    with flask_app_module._pull_lock:
        flask_app_module._pull_state.update(IDLE_STATE)


def _state():
    return flask_app_module._pull_status_snapshot()


# ---------------------------------------------------------------------------
# _watch_pull_process
# ---------------------------------------------------------------------------


class TestWatchPullProcess:
    def test_blank_lines_are_skipped_and_summary_is_reported(self):
        summary = '{"raw": 2, "usable": 2, "inserted": 1, "duplicates": 1}'
        flask_app_module._watch_pull_process(
            _FakeProc(["", "\n", "working...", f"PULL_DATA_SUMMARY::{summary}"])
        )
        state = _state()
        assert state["running"] is False
        assert state["success"] is True
        assert "added 1 new record(s)" in state["message"]
        assert state["finished_at"] is not None

    @pytest.mark.parametrize(
        "summary", ["not json at all", '{"raw": 1}']  # ValueError / KeyError
    )
    def test_unparseable_summary_still_counts_as_success(self, summary):
        flask_app_module._watch_pull_process(_FakeProc([f"PULL_DATA_SUMMARY::{summary}"]))
        state = _state()
        assert state["success"] is True
        assert "couldn't be parsed" in state["message"]

    def test_success_without_summary_line(self):
        flask_app_module._watch_pull_process(_FakeProc(["done"]))
        assert _state()["message"] == "Pull finished."
        assert _state()["success"] is True

    def test_nonzero_exit_reports_last_output_line(self):
        flask_app_module._watch_pull_process(_FakeProc(["a", "boom"], returncode=2))
        state = _state()
        assert state["success"] is False
        assert state["message"] == "Pull failed (exit code 2): boom"

    def test_nonzero_exit_with_no_output(self):
        flask_app_module._watch_pull_process(_FakeProc([], returncode=1))
        assert "no output captured" in _state()["message"]

    def test_watcher_error_is_reported_as_failure(self):
        flask_app_module._watch_pull_process(_ExplodingProc())
        state = _state()
        assert state["success"] is False
        assert "exit code -1" in state["message"]
        assert "status watcher error: pipe broke" in state["message"]


# ---------------------------------------------------------------------------
# start_pull_data / routes
# ---------------------------------------------------------------------------


class TestStartPullData:
    @pytest.mark.parametrize(
        "started_at,expected", [(None, "recently"), (datetime(2026, 1, 1, 9, 5), "09:05 AM")]
    )
    def test_refuses_when_already_running(self, started_at, expected):
        with flask_app_module._pull_lock:
            flask_app_module._pull_state.update(running=True, started_at=started_at)
        started, message = flask_app_module.start_pull_data()
        assert started is False
        assert expected in message

    def test_popen_oserror_is_reported(self, monkeypatch):
        def _raise(*a, **k):
            raise OSError("no python")

        monkeypatch.setattr(flask_app_module.subprocess, "Popen", _raise)
        started, message = flask_app_module.start_pull_data()
        assert started is False
        assert "Could not start Pull Data: no python" in message
        assert _state()["running"] is False

    def test_pull_data_route_returns_409_when_start_loses_race(self, monkeypatch):
        monkeypatch.setattr(flask_app_module, "start_pull_data", lambda: (False, "busy"))
        client = flask_app_module.app.test_client()
        response = client.post("/pull-data")
        assert response.status_code == 409
        assert response.get_json() == {"error": "busy"}

    def test_status_route_serializes_datetimes(self):
        when = datetime(2026, 2, 3, 4, 5, 6)
        with flask_app_module._pull_lock:
            flask_app_module._pull_state.update(started_at=when, finished_at=when)
        client = flask_app_module.app.test_client()
        body = client.get("/pull-data/status").get_json()
        assert body["started_at"] == when.isoformat()
        assert body["finished_at"] == when.isoformat()


# ---------------------------------------------------------------------------
# Analysis trend labels
# ---------------------------------------------------------------------------


def _stub_queries(monkeypatch, term_rows):
    monkeypatch.setattr(flask_app_module, "get_session", lambda: _FakeSessionCtx())
    for name, value in [
        ("question_1", 1),
        ("question_2", 1.0),
        ("question_3", (3.5, 160.0, 155.0, 4.0)),
        ("question_4", 3.5),
        ("question_5", 1.0),
        ("question_6", 3.5),
        ("question_7", 1),
        ("question_8", 1),
        ("question_9", 1),
        ("additional_question_1", []),
        ("additional_question_2", term_rows),
    ]:
        monkeypatch.setattr(flask_app_module.q, name, lambda session, v=value: v)


@pytest.mark.analysis
class TestTrendLabels:
    @pytest.mark.parametrize(
        "term_rows,expected",
        [
            ([("Fall 2025", 10), ("Fall 2026", 20)], "trending up"),
            ([("Fall 2025", 20), ("Fall 2026", 10)], "trending down"),
            ([("Fall 2025", 10), ("Fall 2026", 10)], "no clear trend"),
            ([("Fall 2025", 10)], "no data"),
            ([], "no data"),
        ],
    )
    def test_trend(self, monkeypatch, term_rows, expected):
        _stub_queries(monkeypatch, term_rows)
        context = flask_app_module._build_analysis_context()
        assert context["trend"] == expected
        if not term_rows:
            assert context["term_counts"] == []


# ---------------------------------------------------------------------------
# __main__ blocks
# ---------------------------------------------------------------------------


@pytest.mark.web
class TestMainBlocks:
    def test_app_main_runs_the_dev_server(self, monkeypatch):
        calls = []
        monkeypatch.setattr(flask.Flask, "run", lambda self, **kw: calls.append(kw))
        runpy.run_path(flask_app_module.__file__, run_name="__main__")
        assert calls == [{"debug": True}]


@pytest.mark.db
class TestModels:
    def test_applicant_repr(self):
        a = models.Applicant(p_id=7, program="CS, MIT", term="Fall 2026", status="Accepted")
        assert repr(a) == (
            "Applicant(p_id=7, program='CS, MIT', term='Fall 2026', status='Accepted')"
        )

    def test_get_session_returns_a_session(self):
        session = models.get_session()
        try:
            assert isinstance(session, sqlalchemy.orm.Session)
        finally:
            session.close()

    def test_models_main_prints_row_count(self, monkeypatch, capsys):
        class _Query:
            def count(self):
                return 1234

        class _Session:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def query(self, model):
                return _Query()

        monkeypatch.setattr(sqlalchemy.orm, "sessionmaker", lambda **kw: _Session)
        runpy.run_path(models.__file__, run_name="__main__")
        assert "1,234 row(s)" in capsys.readouterr().out
