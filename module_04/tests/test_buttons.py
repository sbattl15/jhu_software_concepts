from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PULL_DATA_PATH = "/pull-data"
UPDATE_ANALYSIS_PATH = "/update-analysis"
STATUS_PATH = "/pull-data/status"

IDLE_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "success": None,
    "message": "No data pull has been run yet.",
}


class _FakeSessionCtx:
    """Stand-in for `models.get_session()`'s `with ... as session:` block."""

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePullProcess:
    """Stands in for the subprocess.Popen object start_pull_data() gets
    back for pull_data.py. `lines` play the role of what the real
    subprocess would print to stdout -- app.py's background watcher
    thread reads them exactly the same way either way."""

    def __init__(self, lines, returncode=0):
        self.stdout = iter(list(lines))
        self._returncode = returncode

    def wait(self):
        return self._returncode


def _wait_until_idle(flask_app_module, timeout=2.0):
    """The pull pipeline finishes on a background daemon thread started by
    the request handler, so give it a moment to land before asserting on
    the final status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with flask_app_module._pull_lock:
            if not flask_app_module._pull_state["running"]:
                return
        time.sleep(0.01)
    raise AssertionError("pull never finished running within the timeout")


@pytest.fixture
def flask_app_module(monkeypatch):
    import app as flask_app_module

    monkeypatch.setattr(flask_app_module, "get_session", lambda: _FakeSessionCtx())
    for name, value in [
        ("question_1", lambda session: 1234),
        ("question_2", lambda session: 42.0),
        ("question_3", lambda session: (3.80, 165.0, 160.0, 4.5)),
        ("question_4", lambda session: 3.70),
        ("question_5", lambda session: 33.3),
        ("question_6", lambda session: 3.90),
        ("question_7", lambda session: 5),
        ("question_8", lambda session: 10),
        ("question_9", lambda session: 12),
        ("additional_question_1", lambda session: [("3.5-4.0", 20, 55.0)]),
        ("additional_question_2", lambda session: [("Fall 2025", 15), ("Fall 2026", 20)]),
    ]:
        monkeypatch.setattr(flask_app_module.q, name, value)

    # Every test starts from -- and leaves behind -- a clean, idle
    # busy-state: the module (and its module-level _pull_state dict) stays
    # imported and shared across the whole test session, so a test that
    # flips it to "running" would otherwise bleed into the next one.
    with flask_app_module._pull_lock:
        flask_app_module._pull_state.update(IDLE_STATE)

    yield flask_app_module

    with flask_app_module._pull_lock:
        flask_app_module._pull_state.update(IDLE_STATE)


@pytest.fixture
def app(flask_app_module):
    flask_app_module.app.config.update(TESTING=True)
    return flask_app_module.app


@pytest.fixture
def client(app):
    return app.test_client()


def _mark_pull_running(flask_app_module):
    with flask_app_module._pull_lock:
        flask_app_module._pull_state.update(
            running=True,
            started_at=datetime.now(),
            finished_at=None,
            success=None,
            message="Pull Data started...",
        )


# ---------------------------------------------------------------------------
# 2a. POST /pull-data
# ---------------------------------------------------------------------------


class TestPullData:
    def test_returns_200(self, flask_app_module, client, monkeypatch):
        monkeypatch.setattr(
            flask_app_module.subprocess,
            "Popen",
            lambda *a, **k: _FakePullProcess(
                ['PULL_DATA_SUMMARY::{"raw": 1, "usable": 1, "inserted": 1, "duplicates": 0}']
            ),
        )

        response = client.post(PULL_DATA_PATH)
        assert response.status_code == 200

        _wait_until_idle(flask_app_module)

    def test_triggers_the_loader_with_rows_from_the_scraper(
        self, flask_app_module, client, monkeypatch
    ):
        """Fakes the scraped/loaded numbers (5 scraped, 4 usable, 3 newly
        inserted, 1 duplicate) and confirms the real subprocess-launch +
        background-watcher + status-parsing pipeline in app.py actually
        picks them up and reports them, end to end."""
        fake_summary = '{"raw": 5, "usable": 4, "inserted": 3, "duplicates": 1}'
        popen_calls = []

        def fake_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            return _FakePullProcess(
                ["[scrape] fetched 5 raw entries", f"PULL_DATA_SUMMARY::{fake_summary}"]
            )

        monkeypatch.setattr(flask_app_module.subprocess, "Popen", fake_popen)

        response = client.post(PULL_DATA_PATH)
        assert response.status_code == 200
        assert popen_calls, "POST /pull-data never launched the loader subprocess"

        _wait_until_idle(flask_app_module)
        status = client.get(STATUS_PATH).get_json()
        assert status["success"] is True
        assert "3" in status["message"]  # the faked "inserted" count


# ---------------------------------------------------------------------------
# 2b. POST /update-analysis
# ---------------------------------------------------------------------------


class TestUpdateAnalysis:
    def test_returns_200_when_not_busy(self, client):
        response = client.post(UPDATE_ANALYSIS_PATH)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 2c. Busy gating
# ---------------------------------------------------------------------------


class TestBusyGating:
    def test_update_analysis_returns_409_while_pull_running(
        self, flask_app_module, client, monkeypatch
    ):
        _mark_pull_running(flask_app_module)

        # If update_analysis() is properly gated it returns before ever
        # opening a session, so a call here would mean it tried to run
        # the update anyway.
        def _no_query_allowed():
            raise AssertionError(
                "POST /update-analysis queried the database while a pull was running"
            )

        monkeypatch.setattr(flask_app_module, "get_session", _no_query_allowed)

        response = client.post(UPDATE_ANALYSIS_PATH)
        assert response.status_code == 409

    def test_pull_data_returns_409_while_busy(self, flask_app_module, client, monkeypatch):
        _mark_pull_running(flask_app_module)

        def _no_new_process(*args, **kwargs):
            raise AssertionError(
                "POST /pull-data started a new subprocess while a pull was already running"
            )

        monkeypatch.setattr(flask_app_module.subprocess, "Popen", _no_new_process)

        response = client.post(PULL_DATA_PATH)
        assert response.status_code == 409