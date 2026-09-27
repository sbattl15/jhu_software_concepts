Operational Notes
=================

Busy-state policy
-----------------

* Only one data pull can run at a time. The state lives in
  ``app._pull_state`` and is guarded by a lock.
* While a pull is running:

  * ``POST /pull-data`` returns **409** ``{"ok": false, "busy": true}`` and
    starts nothing.
  * ``POST /update-analysis`` returns **409** ``{"ok": false, "busy": true}``
    and runs no queries, so the analysis is never read mid-write.
  * ``GET /analysis`` still works and shows the last committed data.

* If the pull process can't be started at all, ``POST /pull-data`` returns
  **500** ``{"ok": false}`` and the app stays idle.
* ``GET /pull-data/status`` reports ``running``, ``started_at``,
  ``finished_at``, ``success`` and ``message``. The page polls it while a
  pull runs.

Idempotency strategy
--------------------

* **Uniqueness key:** ``applicants.url``. Each GradCafe result has exactly
  one URL, and the column is ``UNIQUE NOT NULL``.
* **Pull Data** (``pull_data.py``) inserts with
  ``INSERT ... ON CONFLICT (url) DO NOTHING``. Re-pulling data that's
  already stored adds nothing and reports it as "duplicates".
* **Bulk load** (``load_data.py``) uses ``ON CONFLICT (url) DO UPDATE``, so
  re-running it refreshes existing rows instead of failing.
* Each pull is written with **one INSERT statement and one commit**. If any
  row fails (e.g. a NOT NULL violation), the transaction is rolled back and
  none of that batch is saved -- no partial writes.
* Rows missing a required field (``program`` or ``url``) are skipped before
  the insert and counted as "not usable".

Required fields
---------------

``program`` and ``url`` are ``NOT NULL``; ``p_id`` is generated. All other
Module 3 columns may be empty when GradCafe didn't provide the value.

Troubleshooting
---------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Symptom
     - Fix
   * - ``db``/``integration`` tests are **skipped**
     - PostgreSQL isn't reachable. Set ``DATABASE_URL`` to a running test
       database, e.g. ``postgresql://postgres:postgres@localhost:5432/gradcafe_test``.
   * - ``No module named 'psycopg2'``
     - The URL was passed straight to SQLAlchemy somewhere. Use
       ``models.normalize_database_url()``, or write the URL as
       ``postgresql+psycopg://...``.
   * - ``password authentication failed``
     - The user/password in ``DATABASE_URL`` is wrong, or ``PGPASSWORD`` is
       stale. Check with ``psql "$DATABASE_URL"``.
   * - Coverage below 100 %
     - Run ``pytest --cov-report=term-missing`` and look at the *Missing*
       column. New code in ``src/`` needs a test.
   * - ``PytestUnknownMarkWarning`` / unknown mark error
     - Only ``web``, ``buttons``, ``analysis``, ``db`` and ``integration``
       are allowed; they are registered in ``pytest.ini``.
   * - Pull Data stays "running" forever
     - The subprocess is stuck (usually the first LLM model download). Check
       the Flask console for ``[pull-data subprocess]`` lines. Restarting the
       app resets the busy flag.
   * - CI: ``connection refused`` to PostgreSQL
     - The ``postgres`` service needs a health check, and ``DATABASE_URL``
       must use ``localhost:5432``.
   * - CI is slow
     - ``llama-cpp-python`` compiles from source. Cache pip
       (``actions/setup-python`` with ``cache: pip``).
   * - Read the Docs build fails with an import error
     - Add the package to ``autodoc_mock_imports`` in ``docs/source/conf.py``
       or to ``docs/requirements.txt``.
