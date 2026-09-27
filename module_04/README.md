# Module 4 — Testing and Documentation

**Name:** Shane Battles (sbattl15)
**Repository (SSH):** `git@github.com:sbattl15/jhu_software_concepts.git`
**Documentation (Read the Docs):** https://sbattl15-test-docs.readthedocs.io/en/latest/

A Grad Café analytics service: it scrapes applicant results from
[The GradCafe](https://www.thegradcafe.com/survey), cleans and standardizes them,
stores them in PostgreSQL, and serves an analysis page built with Flask. This module
adds a Pytest suite with 100% coverage, a GitHub Actions workflow, and Sphinx
documentation.

## Folder layout

```text
module_04/
├── src/
│   ├── load_data.py            # one-time bulk load of the JSON into PostgreSQL
│   ├── query_data.py           # raw-SQL answers printed to the console
│   └── flask_website/
│       ├── app.py              # Flask app: create_app() factory + routes
│       ├── models.py           # SQLAlchemy Applicant model; reads DATABASE_URL
│       ├── orm_queries.py      # analysis queries used by the web page
│       ├── pull_data.py        # scrape -> clean -> LLM -> insert (Pull Data button)
│       ├── scrape.py, clean.py, llm_standardize.py
│       └── templates/index.html
├── tests/                      # all Pytest tests
├── docs/                       # Sphinx project (source/ + built HTML in build/html/)
├── pytest.ini
├── requirements.txt
└── coverage_summary.txt        # terminal coverage report
```

## 1. Set up

Requires Python 3.10+ and PostgreSQL 14+.

```bash
git clone git@github.com:sbattl15/jhu_software_concepts.git
cd jhu_software_concepts/module_04
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configure PostgreSQL

Create a database, then point the app at it with `DATABASE_URL`:

```bash
createdb gradcafe_applications

# macOS/Linux
export DATABASE_URL="postgresql://postgres:<password>@localhost:5432/gradcafe_applications"
# Windows PowerShell
$env:DATABASE_URL = "postgresql://postgres:<password>@localhost:5432/gradcafe_applications"
```

Don't commit real passwords. If `DATABASE_URL` isn't set, the code falls back to
the standard `PGUSER`, `PGPASSWORD`, `PGHOST`, `PGPORT` and `PGDATABASE` variables.

Load the existing data (creates the `applicants` table if needed):

```bash
cd src
python load_data.py --json-file ../llm_extend_applicant_data.json
python query_data.py             # optional: print the analysis answers
```

## 3. Run the Flask app

```bash
cd src/flask_website
python app.py                    # or: flask --app app run
```

Open http://127.0.0.1:5000/analysis. **Pull Data** fetches new GradCafe entries in
the background. **Update Analysis** refreshes the results; it's refused (HTTP 409)
while a pull is running.

| Route | Method | Response |
|---|---|---|
| `/analysis` (also `/`) | GET | The analysis page |
| `/pull-data` | POST | `200 {"ok": true}`; `409 {"busy": true}` if a pull is running; `500 {"ok": false}` if the loader can't start |
| `/pull-data/status` | GET | Current pull state |
| `/update-analysis` | POST | `200 {"ok": true}`; `409 {"busy": true}` while pulling |

## 4. Run the tests

From `module_04` (settings come from `pytest.ini`):

```bash
pytest                                                        # full suite + coverage
pytest -m "web or buttons or analysis or db or integration"   # same suite, by marker
```

Every test is marked `web`, `buttons`, `analysis`, `db` or `integration`, and the
suite fails if coverage of `src/` drops below 100%. The database and integration
tests need `DATABASE_URL` to point at a reachable PostgreSQL database; use a
separate test database. GitHub Actions (`.github/workflows/tests.yml` at the repo
root) starts PostgreSQL and runs the full suite on every push.

## 5. View the documentation

Published at https://sbattl15-test-docs.readthedocs.io/en/latest/.
To build it locally:

```bash
cd docs
make html                         # Windows: .\make.bat html
# then open docs/build/html/index.html
```

## Known bugs

None known.
