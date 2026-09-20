# GradCafe Applicant Analysis -- Flask Webpage (Parts 8-10)

A single dynamic Flask page that reads the `applicants` PostgreSQL table
through the SQLAlchemy `Applicant` model, displays the required analysis
results plus two original questions, and can pull + clean + standardize +
load newly available Grad Cafe entries on demand.

## Files

- `models.py` -- your SQLAlchemy `Applicant` model and engine/session setup (unchanged).
- `orm_queries.py` -- your ORM query functions for all nine required
  questions plus the two original questions, reused (not duplicated) by
  the Flask route.
- `scrape.py` -- your Module 2 scraper (unchanged). Fetches raw Grad Cafe
  entries page by page, respecting robots.txt.
- `clean_new.py` -- your regex cleaner (unchanged). Turns a raw scraped
  entry into a sparse dict of formatted strings (`"GPA 3.70"`, `"GRE 168"`,
  `"Added on Feb 17, 2026"`, key `"US/International"`, ...).
- `llm_standardize.py` -- your local-LLM standardizer (unchanged). Splits
  each entry's combined `program` string into a standardized program name
  + university via a small local model (TinyLlama, via `llama-cpp-python`),
  and also derives the remaining typed fields (parsed date, status,
  GPA/GRE as floats, US/International, degree) from `clean_new.py`'s
  output. Its own Flask app/CLI (`--serve`, `--file ...`) still work
  standalone; the pipeline below only imports its `_call_llm` /
  `_clean_row` functions and calls them in-process.
- `pull_data.py` -- **new.** Orchestrates the full pipeline: scrape ->
  clean -> LLM-standardize -> load into Postgres (`ON CONFLICT (url) DO
  NOTHING`, so re-running never duplicates rows). This is what the Pull
  Data button runs as a subprocess. See the module docstring for the
  step-by-step breakdown.
- `app.py` -- the Flask app.
  - `GET /` -- runs every analysis question and renders the page. Always
    re-queries Postgres, so this route alone satisfies "Update Analysis"
    (re-query + display current results) -- see Part 10 below.
  - `POST /pull-data` -- starts `pull_data.py` as a background subprocess
    (via a module-level lock, so a second click while one is already
    running is refused rather than starting a concurrent scrape), then
    redirects back to `/` with a flash message.
  - `GET /pull-data/status` -- JSON status (`running`, `success`,
    `message`, timestamps) polled by the page's status banner.
- `templates/index.html` / `static/style.css` -- the page markup and
  styling, including the Pull Data / Update Analysis buttons and the live
  status banner.
- `requirements.txt`, this `README.md`.

## Setup

```bash
pip install -r requirements.txt
```

`llama-cpp-python` compiles a C++ extension on install. If plain `pip
install` is slow or fails to build, try:

```bash
pip install --prefer-binary llama-cpp-python
```

to prefer a prebuilt wheel for your platform/Python version if one is
available. It runs CPU-only by default (`N_GPU_LAYERS=0` in
`llm_standardize.py`).

Set the same environment variables `models.py` already expects:

```bash
export PGHOST=localhost
export PGPORT=5432
export PGDATABASE=gradcafe_applications
export PGUSER=postgres
export PGPASSWORD=your_password_here
```

Optional tuning:

```bash
export PULL_DATA_MAX_PAGES=25   # survey pages fetched per Pull Data click (20 entries/page)
export FLASK_SECRET_KEY=change-me   # used only for the one-shot flash message after Pull Data
```

## Run

```bash
flask --app app run --debug
```

Then open http://127.0.0.1:5000/.

## Part 9 -- Pull Data

Clicking **Pull Data** POSTs to `/pull-data`, which launches `pull_data.py`
as a background subprocess and immediately redirects back to `/` with a
status message -- it does not block the page. The button disables itself
(server-side, via a lock, and client-side via a status poll) while a pull
is already running, so a second click can't start a concurrent scrape. The
first Pull Data run downloads the local LLM model (~a few hundred MB) the
first time it's needed, which adds to the run time; later runs reuse the
cached copy under `models/`.

`llm_standardize.py` optionally reads `canon_universities.txt` /
`canon_programs.txt` (one name per line) from the project directory for
fuzzy-matching standardized names against a canonical list -- harmless if
you don't provide them, just less normalization.

## Part 10 -- Update Analysis

No dedicated write logic was needed for this: `/` already re-queries
Postgres and reports live Pull Data status on every request, so reloading
it *is* "re-query and display the most current results." Because it only
ever reads, it can never interfere with (or start) a scrape -- and the
status banner (fed by `/pull-data/status`) tells the user when new data is
still being retrieved, whether they got there via Update Analysis or just
sitting on the page.

## Known limitations / things worth double-checking against your data

- The in-memory Pull Data lock assumes a single Flask process. Running
  under multiple worker processes (e.g. several `gunicorn` workers) would
  need the lock moved to something shared (a row in Postgres, Redis,
  etc.) -- fine for `flask run`'s single dev-server process, not for a
  multi-worker production deployment.
- `pull_data.py` normalizes degree strings (e.g. "MS", "MSc", "Ph.D." ->
  "Masters"/"PhD") to match `orm_queries.py`'s exact-match filters
  (`degree == "PhD"`, `degree == "Masters"`). Verify this against
  `SELECT DISTINCT degree FROM applicants;` on your real data.
- In the `query_data.py` you originally gave me, Question 6's docstring
  says "Fall 2026" but its actual `WHERE` clause filters on `term =
  'Fall 2025'`. The ORM version in `orm_queries.py` matches the *code*
  (Fall 2025) -- flag it if Fall 2026 was actually intended.
