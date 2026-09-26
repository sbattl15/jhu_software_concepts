from __future__ import annotations

import sys
from pathlib import Path

import pytest

# app.py lives at the project root (one level above tests/); make sure it's
# importable regardless of where pytest is invoked from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Every test in this module is a "web" test (page load / HTML structure) --
# see pytest.ini's markers section and the "no unmarked test" policy.
pytestmark = pytest.mark.web


class _FakeSessionCtx:
    """Stand-in for `models.get_session()`'s `with ... as session:` block."""

    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def app(monkeypatch):
    """1a: Build a testable Flask app instance with real routes but a
    stubbed data layer, so no Postgres connection is required."""

    import app as flask_app_module

    # Never touch a real database.
    monkeypatch.setattr(flask_app_module, "get_session", lambda: _FakeSessionCtx())

    # Stub every orm_queries function `_build_analysis_context` calls.
    monkeypatch.setattr(flask_app_module.q, "question_1", lambda session: 1234)
    monkeypatch.setattr(flask_app_module.q, "question_2", lambda session: 42.0)
    monkeypatch.setattr(
        flask_app_module.q, "question_3", lambda session: (3.80, 165.0, 160.0, 4.5)
    )
    monkeypatch.setattr(flask_app_module.q, "question_4", lambda session: 3.70)
    monkeypatch.setattr(flask_app_module.q, "question_5", lambda session: 33.3)
    monkeypatch.setattr(flask_app_module.q, "question_6", lambda session: 3.90)
    monkeypatch.setattr(flask_app_module.q, "question_7", lambda session: 5)
    monkeypatch.setattr(flask_app_module.q, "question_8", lambda session: 10)
    monkeypatch.setattr(flask_app_module.q, "question_9", lambda session: 12)
    monkeypatch.setattr(
        flask_app_module.q,
        "additional_question_1",
        lambda session: [("3.5 - 4.0", 20, 55.0), ("3.0 - 3.5", 10, 30.0)],
    )
    monkeypatch.setattr(
        flask_app_module.q,
        "additional_question_2",
        lambda session: [("Fall 2025", 15), ("Fall 2026", 20)],
    )

    flask_app_module.app.config.update(TESTING=True)
    yield flask_app_module.app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# 1a. Test App factory / config
# ---------------------------------------------------------------------------


class TestAppFactory:
    def test_app_is_a_testable_flask_instance(self, app):
        from flask import Flask

        assert isinstance(app, Flask)
        assert app.testing is True

    @pytest.mark.parametrize(
        "rule,expected_methods",
        [
            ("/", {"GET"}),
            ("/pull-data", {"POST"}),
            ("/pull-data/status", {"GET"}),
        ],
    )
    def test_each_established_route_is_registered(self, app, rule, expected_methods):
        matches = [r for r in app.url_map.iter_rules() if r.rule == rule]
        assert matches, f'no route registered for "{rule}"'
        assert expected_methods.issubset(matches[0].methods)


# ---------------------------------------------------------------------------
# 1b. Test GET / analysis (page load)
# ---------------------------------------------------------------------------


class TestAnalysisPageLoad:
    def test_status_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_page_contains_pull_data_and_update_analysis_buttons(self, client):
        html = client.get("/").get_data(as_text=True)
        assert "Pull Data" in html
        assert "Update Analysis" in html

    def test_page_text_includes_analysis_and_an_answer(self, client):
        html = client.get("/").get_data(as_text=True)
        assert "Analysis" in html
        assert "Answer:" in html