Overview & Setup
================

What it does
------------

The project answers questions about graduate-school admissions -- how many
people applied for a term, acceptance rates, average GPA/GRE scores and so
on -- using applicant-reported results from The GradCafe.

1. **Scrape** survey pages (respecting ``robots.txt``) into raw JSON.
2. **Clean** the raw text into structured fields with regular expressions.
3. **Standardize** program and university names with a local LLM.
4. **Load** the result into a PostgreSQL ``applicants`` table.
5. **Analyze** it from the command line (``query_data.py``) or in a Flask
   dashboard that can also pull new data and refresh its results.

Project layout
--------------

.. code-block:: text

   module_04/
   ├── docs/                     Sphinx documentation (this site)
   ├── src/
   │   ├── load_data.py          one-time bulk load of the JSON into Postgres
   │   ├── query_data.py         raw-SQL answers printed to the console
   │   └── flask_website/
   │       ├── app.py            Flask app: create_app() factory and routes
   │       ├── scrape.py         GradCafe scraper
   │       ├── clean.py          regex cleaner
   │       ├── llm_standardize.py  local-LLM program/university standardizer
   │       ├── pull_data.py      scrape → clean → LLM → load (Pull Data button)
   │       ├── models.py         SQLAlchemy Applicant model; reads DATABASE_URL
   │       ├── orm_queries.py    analysis queries via SQLAlchemy
   │       └── templates/index.html
   ├── tests/                    pytest suite
   ├── pytest.ini
   └── requirements.txt

Requirements
------------

* Python 3.10+
* PostgreSQL 14+
* The packages in ``requirements.txt`` (Flask, psycopg, SQLAlchemy,
  BeautifulSoup4, llama-cpp-python, huggingface_hub, pytest, pytest-cov,
  Sphinx)

Installation
------------

.. code-block:: bash

   git clone git@github.com:sbattl15/jhu_software_concepts.git
   cd jhu_software_concepts/module_04
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt

Environment variables
---------------------

Every module connects to PostgreSQL through ``DATABASE_URL``. If it isn't
set, they fall back to the standard libpq ``PG*`` variables. Never
hard-code a password; set these in your shell or a ``.env`` file that is
excluded from Git.

.. list-table::
   :header-rows: 1
   :widths: 24 26 50

   * - Variable
     - Default
     - Purpose
   * - ``DATABASE_URL``
     - *(none)*
     - PostgreSQL connection URL, e.g.
       ``postgresql://postgres:secret@localhost:5432/gradcafe_applications``.
       Used by ``models.py``, ``load_data.py``, ``query_data.py`` and the
       Pull Data subprocess.
   * - ``PGDATABASE``
     - ``gradcafe_applications``
     - Database name
   * - ``PGUSER``
     - ``postgres``
     - Database role
   * - ``PGPASSWORD``
     - *(none)*
     - Database password (or use ``~/.pgpass``)
   * - ``PGHOST``
     - ``localhost``
     - Database host
   * - ``PGPORT``
     - ``5432``
     - Database port
   * - ``FLASK_SECRET_KEY``
     - ``dev-secret-change-me``
     - Flask session key. **Set a real value outside development.**
   * - ``PULL_DATA_MAX_PAGES``
     - ``25``
     - Survey pages fetched per **Pull Data** click
   * - ``MODEL_REPO`` / ``MODEL_FILE``
     - TinyLlama 1.1B Chat (GGUF)
     - Hugging Face model used by ``llm_standardize.py``
   * - ``N_THREADS``, ``N_CTX``, ``N_GPU_LAYERS``, ``NUM_WORKERS``
     - CPU count, ``2048``, ``0``, ``12``
     - LLM performance tuning

The ``PG*`` variables are only used when ``DATABASE_URL`` is not set.
``postgresql://`` URLs are converted to SQLAlchemy's
``postgresql+psycopg://`` form automatically.

Example (macOS/Linux):

.. code-block:: bash

   export DATABASE_URL="postgresql://postgres:secret@localhost:5432/gradcafe_applications"
   export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex())')"

Windows PowerShell:

.. code-block:: powershell

   $env:DATABASE_URL = "postgresql://postgres:secret@localhost:5432/gradcafe_applications"

Loading the data
----------------

.. code-block:: bash

   cd src
   # upserts every record into DATABASE_URL (creates the table if needed)
   python load_data.py --json-file ../llm_extend_applicant_data.json

   # or create the database first, using the PG* options
   python load_data.py --json-file ../llm_extend_applicant_data.json --create-db --dbname gradcafe_applications

   # print the analysis answers with raw SQL
   python query_data.py

Running the app
---------------

.. code-block:: bash

   cd src/flask_website
   python app.py                 # or: flask --app app run
   # open http://127.0.0.1:5000/analysis

The app is built by the ``create_app()`` factory in ``app.py``. Tests (or
your own scripts) can build a separately configured copy:

.. code-block:: python

   from app import create_app

   app = create_app({"TESTING": True, "DATABASE_URL": "postgresql://localhost/test_db"})

The page lists every analysis question with its answer and has two buttons:

* **Pull Data** -- runs ``pull_data.py`` in the background to scrape, clean,
  standardize and insert new entries. Status is shown under the buttons.
* **Update Analysis** -- re-runs the queries. Refused (HTTP 409) while a
  pull is still running.

Running the tests
-----------------

From ``module_04`` (``pytest.ini`` supplies the Python path and the
coverage options):

.. code-block:: bash

   pytest                                                    # full suite, 100% coverage gate
   pytest -m "web or buttons or analysis or db or integration"
   pytest -m web                                             # one group

The ``db`` and ``integration`` tests that write to PostgreSQL need
``DATABASE_URL`` to point at a reachable **test** database; they skip if it
can't be reached. Coverage is measured over all of ``src/``. See
:doc:`testing` for details.

Building these docs
-------------------

.. code-block:: bash

   pip install -r docs/requirements.txt
   cd docs
   make html                 # Windows: .\make.bat html
   # open docs/build/html/index.html
