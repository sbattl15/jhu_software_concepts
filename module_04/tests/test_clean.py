"""Unit tests for clean.py -- the regex cleaner the "Pull Data" pipeline
runs on raw scraped entries before LLM standardization."""

from __future__ import annotations

import json
import runpy

import pytest

import clean

pytestmark = pytest.mark.buttons

FULL_ENTRY = {
    "raw_school_text": "  Johns Hopkins University ",
    "raw_program_text": "Computer Science Masters",
    "raw_comment_text": (
        "Accepted on Feb 1 Fall 2026 International GRE 168 GRE V 160 "
        "GRE AW 4.50 GPA 3.90 So excited!"
    ),
    "raw_added_on_text": "Feb 17, 2026",
    "raw_decision_text": "Accepted on 1 Feb",
    "raw_meta_text": (
        "Accepted on Feb 1 Fall 2026 International GRE 168 GRE V 160 GRE AW 4.50 GPA 3.90"
    ),
    "url": "https://www.thegradcafe.com/result/1",
}


class TestHelpers:
    @pytest.mark.parametrize("value,expected", [(None, ""), ("  a \n b ", "a b"), (3, "3")])
    def test_clean_text(self, value, expected):
        assert clean._clean_text(value) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [("fall 2026 American", "Fall 2026"), ("no term", "")],
    )
    def test_parse_term(self, text, expected):
        assert clean._parse_term(text) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [("International", "International"), ("american", "American"), ("Other", "")],
    )
    def test_parse_student_type(self, text, expected):
        assert clean._parse_student_type(text) == expected

    def test_score_parsers_find_values_or_return_empty(self):
        text = FULL_ENTRY["raw_meta_text"]
        assert clean._parse_gpa(text) == "GPA 3.90"
        assert clean._parse_gre(text) == "GRE 168"
        assert clean._parse_gre_v(text) == "GRE V 160"
        assert clean._parse_gre_aw(text) == "GRE AW 4.50"
        for parser in (clean._parse_gpa, clean._parse_gre, clean._parse_gre_v, clean._parse_gre_aw):
            assert parser("nothing here") == ""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Speech Language Pathology Masters", ("Speech Language Pathology", "Masters")),
            ("Physics PhD", ("Physics", "PhD")),
            ("Physics", ("Physics", "")),
        ],
    )
    def test_extract_degree(self, text, expected):
        assert clean._extract_degree(text) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Rejected on Feb 3 Fall 2026 American GPA 3.5 Oh well", "Oh well"),
            ("Just a comment", "Just a comment"),
        ],
    )
    def test_extract_comment(self, text, expected):
        assert clean._extract_comment(text) == expected


class TestCleanData:
    def test_full_entry_produces_every_field(self):
        (row,) = clean.clean_data([FULL_ENTRY])
        assert row == {
            "program": "Computer Science, Johns Hopkins University",
            "comments": "So excited!",
            "date_added": "Added on Feb 17, 2026",
            "url": "https://www.thegradcafe.com/result/1",
            "status": "Accepted on 1 Feb",
            "term": "Fall 2026",
            "US/International": "International",
            "GRE": "GRE 168",
            "GRE V": "GRE V 160",
            "GRE AW": "GRE AW 4.50",
            "GPA": "GPA 3.90",
            "Degree": "Masters",
        }

    def test_empty_entry_only_has_comments(self):
        assert clean.clean_data([{}]) == [{"comments": ""}]

    def test_added_on_prefix_is_not_doubled(self):
        (row,) = clean.clean_data([{"raw_added_on_text": "Added on Feb 1, 2026"}])
        assert row["date_added"] == "Added on Feb 1, 2026"


class TestIO:
    def test_load_data_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            clean.load_data(str(tmp_path / "missing.json"))

    def test_save_then_load_round_trips(self, tmp_path, capsys):
        path = tmp_path / "out.json"
        clean.save_data([{"a": "é"}], str(path))
        assert clean.load_data(str(path)) == [{"a": "é"}]
        assert "Wrote 1 entries" in capsys.readouterr().out

    def test_save_data_wraps_os_errors(self, tmp_path):
        with pytest.raises(RuntimeError, match="Could not write output file"):
            clean.save_data([], str(tmp_path / "no-such-dir" / "out.json"))


class TestMain:
    def test_missing_input_returns_1(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr("sys.argv", ["clean.py", "-i", str(tmp_path / "missing.json")])
        assert clean.main() == 1
        assert "[error]" in capsys.readouterr().err

    def test_empty_input_writes_nothing(self, monkeypatch, tmp_path, capsys):
        src = tmp_path / "in.json"
        src.write_text("[]", encoding="utf-8")
        out = tmp_path / "out.json"
        monkeypatch.setattr("sys.argv", ["clean.py", "-i", str(src), "-o", str(out)])
        assert clean.main() == 0
        assert not out.exists()
        assert "Nothing was written" in capsys.readouterr().out

    def test_cleans_input_file(self, monkeypatch, tmp_path):
        src = tmp_path / "in.json"
        src.write_text(json.dumps([FULL_ENTRY]), encoding="utf-8")
        out = tmp_path / "out.json"
        monkeypatch.setattr("sys.argv", ["clean.py", "-i", str(src), "-o", str(out)])
        assert clean.main() == 0
        assert json.loads(out.read_text(encoding="utf-8"))[0]["Degree"] == "Masters"

    def test_module_main_block(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.argv", ["clean.py", "-i", str(tmp_path / "missing.json")])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(clean.__file__, run_name="__main__")
        assert exc_info.value.code == 1
