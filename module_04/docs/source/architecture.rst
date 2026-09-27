Architecture
============

The system has three layers. Each one has a single responsibility, and they
hand data to each other through a JSON file or the ``applicants`` table.

.. code-block:: text

   ┌─────────────────────────── ETL layer ────────────────────────────┐
   │ scrape.py ─raw─► clean.py ─clean─► llm_standardize.py ─► load    │
   │   (extract)        (transform)      (standardize)   load_data.py │
   │                                                     pull_data.py │
   └───────────────────────────────────────────────────────────┬──────┘
                                                               ▼
   ┌─────────────────────────── DB layer ─────────────────────────────┐
   │  PostgreSQL "applicants" table                                   │
   │  models.py (SQLAlchemy Applicant) ◄── orm_queries.py             │
   │  query_data.py (raw SQL via psycopg)                             │
   └───────────────────────────────────────────────────────────┬──────┘
                                                               ▼
   ┌─────────────────────────── Web layer ────────────────────────────┐
   │  app.py (Flask) → templates/index.html                           │
   │  Pull Data button → background pull_data.py subprocess           │
   │  Update Analysis button → re-run orm_queries                     │
   └──────────────────────────────────────────────────────────────────┘

Web layer
---------

**Module:** :mod:`app` (``src/flask_website/app.py``)

* :func:`app.create_app` builds the Flask app and registers every route. It
  accepts a config dict, e.g. ``{"TESTING": True, "DATABASE_URL": ...}``,
  so tests can run against their own database. The module-level ``app`` is
  ``create_app()`` with the defaults.
* ``GET /analysis`` (and ``/``) calls :func:`app._build_analysis_context`, which re-queries the
  database through ``orm_queries`` on every request, so the page always
  shows current numbers.
* ``POST /pull-data`` starts ``pull_data.py`` as a **background subprocess**
  so a long scrape never blocks the page. A daemon thread,
  :func:`app._watch_pull_process`, reads its output and records the result.
* ``GET /pull-data/status`` returns the pull state so the page can poll it.
* ``POST /update-analysis`` re-runs the queries.
* A lock-protected ``_pull_state`` dict works as a busy flag: a second pull,
  or an update during a pull, gets **HTTP 409 Conflict**.

.. list-table:: Routes
   :header-rows: 1

   * - Route
     - Method
     - Success
     - When busy
   * - ``/analysis`` and ``/``
     - GET
     - 200, rendered ``index.html``
     - --
   * - ``/pull-data``
     - POST
     - 200 ``{"ok": true, "started": true, "message": ...}``;
       500 ``{"ok": false, "error": ...}`` if the loader can't start
     - 409 ``{"ok": false, "busy": true, "error": ...}``
   * - ``/pull-data/status``
     - GET
     - 200 ``{"running", "started_at", "finished_at", "success", "message"}``
     - --
   * - ``/update-analysis``
     - POST
     - 200 ``{"ok": true, "updated": true, "generated_at": ...}``
     - 409 ``{"ok": false, "busy": true, "error": ...}``, no query run

ETL layer
---------

**Extract -- :mod:`scrape`**

* Checks ``robots.txt`` before any request and refuses if the survey path
  is disallowed.
* Follows each page's ``Next`` link (GradCafe uses cursor pagination), one
  page at a time, with a politeness delay.
* Stops on HTTP 403/429/503, CAPTCHA pages, network errors or
  ``MAX_PAGES``.
* Returns raw text fields only and removes duplicate result IDs.

**Transform -- :mod:`clean`**

* Uses regular expressions to split raw text into ``program``, ``Degree``,
  ``status``, ``term``, ``US/International``, ``GRE``, ``GRE V``,
  ``GRE AW``, ``GPA``, ``date_added`` and the free-text ``comments``.

**Standardize -- ``llm_standardize``**

* Runs each distinct program string through a local TinyLlama model to
  produce ``llm-generated-program`` and ``llm-generated-university``, then
  snaps results to canonical name lists.

**Load -- :mod:`load_data` and :mod:`pull_data`**

* :mod:`load_data` does the one-time bulk load: it parses strings into typed
  values, creates the table and indexes if missing, and upserts on ``url``
  in one transaction (any error rolls back the whole batch).
* :mod:`pull_data` does incremental loads for the **Pull Data** button:
  scrape → clean → LLM → ``INSERT ... ON CONFLICT (url) DO NOTHING``, so
  clicking it again never creates duplicates. It ends by printing a
  ``PULL_DATA_SUMMARY::{json}`` line that the web layer reads.

DB layer
--------

``applicants`` table (defined in ``load_data.CREATE_TABLE_SQL`` and mirrored
by the SQLAlchemy ``Applicant`` model in ``models.py``):

.. list-table::
   :header-rows: 1
   :widths: 32 16 52

   * - Column
     - Type
     - Meaning
   * - ``p_id``
     - SERIAL PK
     - Surrogate key
   * - ``program``
     - TEXT NOT NULL
     - Program and university as scraped
   * - ``comments``
     - TEXT
     - Applicant's free-text comment
   * - ``date_added``
     - DATE
     - When the entry was posted
   * - ``url``
     - TEXT UNIQUE NOT NULL
     - GradCafe result link (natural key)
   * - ``status``
     - TEXT
     - Accepted / Rejected / Interview / Wait listed
   * - ``term``
     - TEXT
     - Start term, e.g. ``Fall 2026``
   * - ``us_or_international``
     - TEXT
     - ``American`` or ``International``
   * - ``gpa``, ``gre``, ``gre_v``, ``gre_aw``
     - REAL
     - Reported scores
   * - ``degree``
     - TEXT
     - ``Masters``, ``PhD``, ...
   * - ``llm_generated_program``, ``llm_generated_university``
     - TEXT
     - Standardized names from the LLM step

Indexes cover ``llm_generated_university``, ``llm_generated_program``,
``term`` and ``degree``.

``models.py`` reads the connection from ``DATABASE_URL`` (falling back to
the ``PG*`` variables), and :func:`app.create_app` can override it with
``models.configure_database()``.

Two query paths answer the same questions:

* :mod:`query_data` -- raw SQL through psycopg, printed to the console.
* ``orm_queries`` -- SQLAlchemy expressions over ``models.Applicant``, used
  by the Flask app.

Pull Data sequence
------------------

1. The user clicks **Pull Data** and the page sends ``POST /pull-data``.
2. :func:`app.start_pull_data` checks the busy flag and launches
   ``pull_data.py``.
3. ``pull_data.py`` scrapes, cleans, standardizes and inserts new rows, then
   prints ``PULL_DATA_SUMMARY::{"inserted": ..., "duplicates": ..., ...}``.
4. :func:`app._watch_pull_process` parses that line, stores the message
   and clears the busy flag.
5. The user clicks **Update Analysis** (or reloads) and sees the new
   numbers.
