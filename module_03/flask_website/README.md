# GradCafe Applicant Analysis -- Flask Webpage (Part 8A)

A single dynamic Flask page that reads the `applicants` PostgreSQL table
through the SQLAlchemy `Applicant` model and displays the required
analysis results plus two original questions.

## Files

- `models.py` -- your existing SQLAlchemy `Applicant` model and engine/session setup (unchanged).
- `orm_queries.py` -- your existing ORM query functions, extended with the
  few that were only in `query_data.py` (raw-SQL Questions 2, 3, 6, 7, and a
  second original question) so every result the page needs is available as
  a reusable, tested function rather than duplicated inline in the route.
- `app.py` -- the Flask app. One route (`/`) calls the `orm_queries`
  functions and renders `templates/index.html`.
- `templates/index.html` -- the page markup.
- `static/style.css` -- page styling (light/dark mode aware).

## Setup

```bash
pip install -r requirements.txt
```

Set the same environment variables `models.py` already expects (these all
have sane local defaults except the password):

```bash
export PGHOST=localhost
export PGPORT=5432
export PGDATABASE=gradcafe_applications
export PGUSER=postgres
export PGPASSWORD=your_password_here
```

## Run

```bash
flask --app app run --debug
```

Then open http://127.0.0.1:5000/ -- the page queries Postgres on every
request, so it always reflects the current contents of the `applicants`
table.

## Notes

- All required questions (1-9) plus two original questions (acceptance
  rate by GPA bucket, and applicant volume by term with a trending
  up/down badge) are computed via SQLAlchemy against the `Applicant`
  model -- no raw SQL and no duplicated query logic inside `app.py`.
- One thing worth double-checking against your own data: in the
  `query_data.py` you provided, Question 6's docstring says "Fall 2026"
  but its actual `WHERE` clause filters on `term = 'Fall 2025'`. The ORM
  version here matches the *code* (Fall 2025) and the docstring has been
  corrected to match -- flag it if Fall 2026 was actually intended.
