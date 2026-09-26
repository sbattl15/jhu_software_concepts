"""tests/test_analysis_format.py

Part 3: Analysis Formatting.

    3a. Test labels & rounding
        i.  Test that your page includes "Answer" labels for rendered
            analysis.
        ii. Test that any percentage is formatted with two decimals.

Same hermetic approach as the other test files here: the ORM session
and every `orm_queries.question_*` / `additional_question_*` function
are monkeypatched so no live Postgres is required. Deliberately odd
mock values are used (a plain int with no decimal part, a value with
only one decimal, an int-valued acceptance rate, a term split that
divides unevenly) specifically to catch formatting that only happens
to look right for "nice" numbers -- ".00"/".0"/"" all collapse to a
correctly-rounded number, but only "%.2f"-style formatting produces
".00" (rather than "" or ".0") every time.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Matches any "<digits>.<digits>%" in the rendered page -- deliberately not
# scoped to a particular CSS class, so it also catches a percentage this
# test didn't anticipate (e.g. a new tile, or the term-volume bar widths)
# rather than only the ones enumerated by hand.
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")


class _FakeSessionCtx:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def app(monkeypatch):
    import app as flask_app_module

    monkeypatch.setattr(flask_app_module, "get_session", lambda: _FakeSessionCtx())

    # Deliberately awkward values: an int percentage with no decimal part
    # at all (42), a percentage with only one decimal (33.3), an
    # int-valued GPA-bucket acceptance rate (55), and a term split (13 of
    # 20) that divides unevenly, so a bar-width computation without
    # explicit rounding would produce a long, non-2-decimal float.
    monkeypatch.setattr(flask_app_module.q, "question_1", lambda session: 1234)
    monkeypatch.setattr(flask_app_module.q, "question_2", lambda session: 42)
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
        lambda session: [("3.5 - 4.0", 20, 55), ("3.0 - 3.5", 9, None)],
    )
    monkeypatch.setattr(
        flask_app_module.q,
        "additional_question_2",
        lambda session: [("Fall 2025", 13), ("Fall 2026", 17)],
    )

    flask_app_module.app.config.update(TESTING=True)
    return flask_app_module.app


@pytest.fixture
def page_html(app):
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


class TestAnswerLabels:
    def test_page_includes_answer_labels(self, page_html):
        assert "Answer:" in page_html

    def test_every_rendered_result_has_its_own_answer_label(self, page_html):
        # 9 required-analysis results are rendered (Q1-Q9); each tile should
        # carry its own "Answer:" label, not just one shared label on the
        # page somewhere.
        assert page_html.count("Answer:") >= 9


class TestPercentageRounding:
    def test_any_percentage_on_the_page_has_two_decimals(self, page_html):
        percentages = PERCENT_RE.findall(page_html)
        assert percentages, "no percentage-shaped values found on the page to check"
        not_two_decimals = [p for p in percentages if not re.fullmatch(r"\d+\.\d{2}", p)]
        assert not not_two_decimals, (
            f"found percentage(s) not formatted to two decimals: {not_two_decimals} "
            f"(all percentages found: {percentages})"
        )

    def test_int_valued_percentage_still_gets_two_decimals(self, page_html):
        # question_2 is mocked as the plain int 42 -- naive "{value}%"
        # formatting would render "42%", not "42.00%".
        assert "42.00%" in page_html
        assert "42%" not in page_html

    def test_one_decimal_percentage_gets_padded_to_two(self, page_html):
        # question_5 is mocked as 33.3 -- naive "{value}%" formatting
        # would render "33.3%", not "33.30%".
        assert "33.30%" in page_html

    def test_uneven_division_is_rounded_to_two_decimals(self, page_html):
        # The term-volume bar width divides 13/17 * 100 = 76.47058823...%,
        # a repeating decimal -- this must come out rounded to exactly two
        # places, not truncated ("76.4") or left at full float precision
        # ("76.47058823529412").
        assert "76.47%" in page_html

    def test_missing_acceptance_rate_renders_as_zero_not_none(self, page_html):
        # additional_question_1's second GPA bucket has no accepted rows
        # among 9 applicants, so orm_queries hands back None rather than
        # 0 -- this must still render as "0.00%", not "None%" or a
        # division-by-zero error.
        assert "0.00%" in page_html
        assert "None%" not in page_html