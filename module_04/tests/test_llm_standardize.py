"""Unit tests for llm_standardize.py. The model download
(hf_hub_download) and llama.cpp (Llama) are always replaced with fakes,
so these run offline and never load a real model."""

from __future__ import annotations

import json
import re
import runpy

import flask
import huggingface_hub
import llama_cpp
import pytest

import llm_standardize as llm

pytestmark = pytest.mark.buttons


class _FakeLlama:
    """Stands in for llama_cpp.Llama; replies with `reply` every time."""

    reply = '{"standardized_program": "computer science", "standardized_university": "mit"}'

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.reply}}]}


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """Every test starts with no cached model, and any attempt to download
    or load one gets a fake instead of hitting the network/CPU."""
    monkeypatch.setattr(llm, "_LLM", None)
    monkeypatch.setattr(llm, "hf_hub_download", lambda **kw: "/fake/model.gguf")
    monkeypatch.setattr(llm, "Llama", _FakeLlama)
    monkeypatch.setattr(llm, "CANON_UNIS", [])
    monkeypatch.setattr(llm, "CANON_PROGS", [])


def _fake_llm_with_reply(monkeypatch, reply):
    fake = _FakeLlama()
    fake.reply = reply
    monkeypatch.setattr(llm, "_load_llm", lambda n_threads=None: fake)
    return fake


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------


class TestReadLines:
    def test_reads_non_empty_stripped_lines(self, tmp_path):
        path = tmp_path / "canon.txt"
        path.write_text("  MIT \n\nStanford\n", encoding="utf-8")
        assert llm._read_lines(str(path)) == ["MIT", "Stanford"]

    def test_missing_file_is_empty(self, tmp_path):
        assert llm._read_lines(str(tmp_path / "missing.txt")) == []


class TestParsers:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, None),
            ("Feb 17, 2026", None),
            ("Added on Someday", None),
            ("Added on Feb 17, 2026", "2026-02-17"),
        ],
    )
    def test_parse_date_added(self, value, expected):
        assert llm._parse_date_added(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, (None, None)),
            ("Pending", (None, None)),
            ("Wait listed on 3 Mar", ("Wait listed", "3 Mar")),
        ],
    )
    def test_parse_status(self, value, expected):
        assert llm._parse_status(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [(None, None), ("3.9", None), ("GPA 3.8.5", None), (" GPA 3.85 ", 3.85)],
    )
    def test_parse_gpa(self, value, expected):
        assert llm._parse_gpa(value) == expected

    @pytest.mark.parametrize(
        "value,expected", [(None, None), ("GRE abc", None), ("GRE AW 4.5", 4.5)]
    )
    def test_parse_score(self, value, expected):
        assert llm._parse_score(value) == expected

    def test_parse_score_unparseable_number(self, monkeypatch):
        monkeypatch.setattr(llm, "SCORE_RE", re.compile(r"(?P<value>\S+)\s*$"))
        assert llm._parse_score("GRE 1.2.3") is None

    def test_clean_row_derives_every_field(self):
        row = {
            "program": "CS, MIT",
            "comments": "",
            "url": "u",
            "term": "Fall 2026",
            "date_added": "Added on Feb 17, 2026",
            "status": "Accepted on 1 Feb",
            "US/International": "American",
            "Degree": "PhD",
            "GPA": "GPA 3.90",
            "GRE": "GRE 168",
            "GRE V": "GRE V 160",
            "GRE AW": "GRE AW 4.5",
        }
        llm._clean_row(row, {"standardized_program": "CS", "standardized_university": "MIT"})
        assert row["llm-generated-program"] == "CS"
        assert row["llm-generated-university"] == "MIT"
        assert row["comments"] is None
        assert row["date_added"] == "2026-02-17"
        assert row["status"] == "Accepted"
        assert row["accepted_date"] == "1 Feb"
        assert row["rejected_date"] is None
        assert (row["gpa"], row["gre"], row["gre_v"], row["gre_aw"]) == (3.9, 168.0, 160.0, 4.5)
        assert row["degree"] == "PhD"
        assert row["us_or_international"] == "American"


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


class TestModelLoading:
    def test_ensure_model_downloaded_passes_repo_and_file(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(llm, "hf_hub_download", lambda **kw: seen.update(kw) or "/m.gguf")
        assert llm._ensure_model_downloaded() == "/m.gguf"
        assert seen["repo_id"] == llm.MODEL_REPO
        assert seen["filename"] == llm.MODEL_FILE

    def test_load_llm_creates_once_then_reuses(self):
        first = llm._load_llm()
        assert first.kwargs["model_path"] == "/fake/model.gguf"
        assert first.kwargs["n_threads"] == llm.N_THREADS
        assert llm._load_llm() is first

    def test_load_llm_with_explicit_thread_count(self):
        assert llm._load_llm(n_threads=3).kwargs["n_threads"] == 3

    def test_pool_worker_init_sets_threads_and_loads(self, monkeypatch):
        monkeypatch.setattr(llm, "N_THREADS", llm.N_THREADS)  # restored afterwards
        llm._pool_worker_init(2)
        assert llm.N_THREADS == 2
        assert llm._LLM.kwargs["n_threads"] == 2


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    @pytest.mark.parametrize(
        "text,expected",
        [
            # (the final title-casing step turns "McGill" into "Mcgill")
            ("Information, McG", ("Information", "Mcgill University")),
            ("Math at U.B.C.", ("Math", "University of British Columbia")),
            ("history, university of toronto", ("History", "University of Toronto")),
            ("Physics", ("Physics", "Unknown")),
            ("", ("", "Unknown")),
            (None, ("", "Unknown")),
        ],
    )
    def test_split_fallback(self, text, expected):
        assert llm._split_fallback(text) == expected

    def test_best_match(self):
        assert llm._best_match("", ["a"]) is None
        assert llm._best_match("x", []) is None
        assert llm._best_match("Stanfrd University", ["Stanford University"]) == (
            "Stanford University"
        )
        assert llm._best_match("zzz", ["Stanford University"]) is None

    def test_post_normalize_program(self, monkeypatch):
        monkeypatch.setattr(llm, "CANON_PROGS", ["Mathematics", "Computer Science"])
        assert llm._post_normalize_program("Mathematic") == "Mathematics"  # common fix
        assert llm._post_normalize_program("computer scienc") == "Computer Science"  # fuzzy
        assert llm._post_normalize_program("underwater basket weaving") == (
            "Underwater Basket Weaving"
        )
        assert llm._post_normalize_program(None) == ""

    def test_post_normalize_university(self, monkeypatch):
        monkeypatch.setattr(
            llm, "CANON_UNIS", ["McGill University", "University of British Columbia"]
        )
        assert llm._post_normalize_university("UBC") == "University of British Columbia"
        assert llm._post_normalize_university("McGiill University") == "McGill University"
        assert llm._post_normalize_university("Mcgil University") == "McGill University"
        assert llm._post_normalize_university("some college") == "Some College"
        assert llm._post_normalize_university("") == "Unknown"


# ---------------------------------------------------------------------------
# _call_llm + Flask endpoints
# ---------------------------------------------------------------------------


class TestCallLlm:
    def test_json_reply_is_parsed_and_normalized(self, monkeypatch):
        fake = _fake_llm_with_reply(
            monkeypatch,
            'Sure! {"standardized_program": "computer science", '
            '"standardized_university": "mit"} hope that helps',
        )
        assert llm._call_llm("CS, MIT") == {
            "standardized_program": "Computer Science",
            "standardized_university": "Mit",
        }
        messages = fake.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert json.loads(messages[-1]["content"]) == {"program": "CS, MIT"}
        assert len(messages) == 2 + 2 * len(llm.FEW_SHOTS)

    @pytest.mark.parametrize("reply", ["not json at all", None])
    def test_non_json_reply_falls_back_to_rules(self, monkeypatch, reply):
        _fake_llm_with_reply(monkeypatch, reply)
        assert llm._call_llm("physics, stanford university") == {
            "standardized_program": "Physics",
            "standardized_university": "Stanford University",
        }

    def test_worker_entry_point_calls_llm(self, monkeypatch):
        monkeypatch.setattr(llm, "_call_llm", lambda text: {"echo": text})
        assert llm._worker_call_llm("x") == {"echo": "x"}


class TestFlaskEndpoints:
    @pytest.mark.parametrize(
        "payload,expected",
        [([{"a": 1}], [{"a": 1}]), ({"rows": [{"a": 1}]}, [{"a": 1}]), ({"x": 1}, []), (None, [])],
    )
    def test_normalize_input(self, payload, expected):
        assert llm._normalize_input(payload) == expected

    def test_health(self):
        response = llm.app.test_client().get("/")
        assert response.get_json() == {"ok": True}

    def test_standardize(self, monkeypatch):
        monkeypatch.setattr(
            llm,
            "_call_llm",
            lambda text: {"standardized_program": f"P({text})", "standardized_university": "U"},
        )
        response = llm.app.test_client().post(
            "/standardize", json={"rows": [{"program": "CS, MIT"}, {}]}
        )
        rows = response.get_json()["rows"]
        assert rows[0]["llm-generated-program"] == "P(CS, MIT)"
        assert rows[1]["llm-generated-program"] == "P()"


# ---------------------------------------------------------------------------
# CLI file processing
# ---------------------------------------------------------------------------


class _FakeExecutor:
    """Runs ProcessPoolExecutor's work serially in-process."""

    instances: list = []

    def __init__(self, max_workers, initializer, initargs):
        self.max_workers = max_workers
        initializer(*initargs)
        _FakeExecutor.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, items):
        return [fn(item) for item in items]


def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


ROWS = [
    {"program": "CS, MIT", "url": "u1"},
    {"program": "CS, MIT", "url": "u2"},
    {"program": "Math, UBC", "url": "u3"},
]


@pytest.fixture
def fake_call_llm(monkeypatch):
    calls = []

    def _fake(text):
        calls.append(text)
        return {"standardized_program": f"P({text})", "standardized_university": "U"}

    monkeypatch.setattr(llm, "_call_llm", _fake)
    return calls


class TestCliProcessFile:
    def test_single_worker_to_stdout_dedupes_llm_calls(
        self, tmp_path, capsys, fake_call_llm, monkeypatch
    ):
        monkeypatch.setattr(llm, "_CHECKPOINT_EVERY", 2)
        in_path = _write_json(tmp_path / "in.json", {"rows": ROWS})
        llm._cli_process_file(in_path, None, append=False, to_stdout=True, workers=1)
        assert fake_call_llm == ["CS, MIT", "Math, UBC"]
        captured = capsys.readouterr()
        final = json.loads(captured.out[captured.out.rindex("[\n") :])
        assert [row["url"] for row in final] == ["u1", "u2", "u3"]
        assert "3 pending rows -> 2 distinct" in captured.err

    def test_default_out_path_and_process_pool(self, tmp_path, monkeypatch):
        monkeypatch.setattr(llm, "ProcessPoolExecutor", _FakeExecutor)
        monkeypatch.setattr(llm, "N_THREADS", llm.N_THREADS)
        monkeypatch.setattr(
            _FakeLlama, "reply", '{"standardized_program": "x", "standardized_university": "y"}'
        )
        in_path = _write_json(tmp_path / "in.json", ROWS)
        llm._cli_process_file(in_path, None, append=False, to_stdout=False, workers=3)
        out = json.loads((tmp_path / "in.json.out.json").read_text(encoding="utf-8"))
        assert len(out) == 3
        assert _FakeExecutor.instances[-1].max_workers == 3

    def test_append_resumes_and_skips_known_urls(self, tmp_path, fake_call_llm):
        in_path = _write_json(tmp_path / "in.json", ROWS)
        out_path = tmp_path / "out.json"
        _write_json(out_path, [{"url": "u1", "done": True}, "junk"])
        llm._cli_process_file(in_path, str(out_path), append=True, to_stdout=False, workers=0)
        out = json.loads(out_path.read_text(encoding="utf-8"))
        assert [r["url"] if isinstance(r, dict) else r for r in out] == ["u1", "junk", "u2", "u3"]

    def test_append_with_nothing_new_just_rewrites_output(self, tmp_path, fake_call_llm):
        in_path = _write_json(tmp_path / "in.json", ROWS[:1])
        out_path = tmp_path / "out.json"
        _write_json(out_path, [{"url": "u1"}])
        llm._cli_process_file(in_path, str(out_path), append=True, to_stdout=False, workers=1)
        assert fake_call_llm == []
        assert json.loads(out_path.read_text(encoding="utf-8")) == [{"url": "u1"}]

    @pytest.mark.parametrize("existing", ["{not valid json", '{"rows": []}'])
    def test_append_ignores_corrupt_or_non_list_output(self, tmp_path, fake_call_llm, existing):
        in_path = _write_json(tmp_path / "in.json", ROWS[:1])
        out_path = tmp_path / "out.json"
        out_path.write_text(existing, encoding="utf-8")
        llm._cli_process_file(in_path, str(out_path), append=True, to_stdout=False, workers=1)
        assert len(json.loads(out_path.read_text(encoding="utf-8"))) == 1

    def test_empty_input_to_stdout(self, tmp_path, capsys, fake_call_llm):
        in_path = _write_json(tmp_path / "in.json", [])
        llm._cli_process_file(in_path, None, append=False, to_stdout=True, workers=1)
        assert json.loads(capsys.readouterr().out) == []


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------


class TestMainBlock:
    def test_serve_runs_flask(self, monkeypatch):
        calls = []
        monkeypatch.setattr(flask.Flask, "run", lambda self, **kw: calls.append(kw))
        monkeypatch.setattr("sys.argv", ["llm_standardize.py", "--serve"])
        monkeypatch.setenv("PORT", "8123")
        runpy.run_path(llm.__file__, run_name="__main__")
        assert calls == [{"host": "0.0.0.0", "port": 8123, "debug": False}]

    def test_file_mode_processes_the_file(self, monkeypatch, tmp_path, capsys):
        # The re-executed module imports these names afresh, so patch them
        # at their source packages.
        monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda **kw: "/fake.gguf")
        monkeypatch.setattr(llama_cpp, "Llama", _FakeLlama)
        in_path = _write_json(tmp_path / "in.json", ROWS[:1])
        monkeypatch.setattr(
            "sys.argv",
            ["llm_standardize.py", "--file", in_path, "--stdout", "--workers", "1"],
        )
        runpy.run_path(llm.__file__, run_name="__main__")
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["url"] == "u1"
