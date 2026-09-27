Testing Guide
=============

The suite lives in ``module_04/tests`` and uses pytest with pytest-cov.
``pytest.ini`` puts both source folders on the import path, registers the
markers and enforces **100 % coverage of everything under** ``src/``:

.. code-block:: ini

   [pytest]
   testpaths = tests
   pythonpath = src/flask_website src
   addopts = -q --cov=src --cov-report=term-missing --cov-fail-under=100
   markers =
       web: Flask route/page tests
       buttons: "Pull Data" and "Update Analysis" behavior
       analysis: formatting/rounding of analysis output
       db: database schema/inserts/selects
       integration: end-to-end flows

Running marked tests
--------------------

Run everything from ``module_04``:

.. code-block:: bash

   pytest                                                      # full suite
   pytest -m "web or buttons or analysis or db or integration" # every test, by marker
   pytest -m web                                               # page rendering only
   pytest -m buttons                                           # button + pipeline tests
   pytest -m "not integration"                                 # skip end-to-end flows

Every test file sets a module-level ``pytestmark``, so no test is unmarked
and the marker expression above runs the entire suite.

.. list-table::
   :header-rows: 1
   :widths: 16 34 50

   * - Marker
     - Files
     - What is checked
   * - ``web``
     - ``test_flask_page.py`` (plus ``TestCreateApp``, ``TestMainBlocks``)
     - ``create_app()`` builds a configured app with every route;
       ``GET /analysis`` returns 200 and shows both buttons, "Analysis" and
       ``Answer:`` labels.
   * - ``buttons``
     - ``test_buttons.py``, ``test_app_internals.py``, ``test_pull_data.py``,
       ``test_scrape.py``, ``test_clean.py``, ``test_llm_standardize.py``
     - ``POST /pull-data`` returns 200 ``{"ok": true}`` and launches the
       loader; 409 ``{"busy": true}`` while a pull runs; 500 and no state
       change if the loader can't start. ``POST /update-analysis`` returns
       200 and re-runs the queries when idle, 409 ``{"busy": true}`` with no
       query when busy. Also every stage the Pull Data button runs.
   * - ``analysis``
     - ``test_analysis_format.py`` (plus ``TestTrendLabels``)
     - Every answer element starts with ``Answer:``, and every percentage
       has exactly two decimals.
   * - ``db``
     - ``test_db_insert.py``, ``test_orm_queries.py``,
       ``test_load_data.py``, ``test_query_data.py`` (plus ``TestModels``)
     - A pull inserts rows with every required non-null field; re-pulling
       is idempotent; a failing batch writes nothing; the query function
       returns a dict with the required keys; ``DATABASE_URL`` handling;
       ``load_data.py`` and ``query_data.py`` end to end with a fake
       connection.
   * - ``integration``
     - ``test_integration_end_to_end.py``
     - A fake scraper returns several records → ``POST /pull-data`` →
       ``POST /update-analysis`` → ``GET /analysis`` shows the new rows,
       correctly formatted; overlapping pulls don't create duplicates.

Expected selectors
------------------

UI tests find elements with BeautifulSoup using stable ``data-testid``
attributes in ``templates/index.html``. The matching ``id`` attributes are
used by the page's JavaScript. Don't rename either without updating the
tests.

.. list-table::
   :header-rows: 1

   * - Element
     - Selector
   * - Pull Data button
     - ``button[data-testid="pull-data-btn"]`` (also ``#pull-data-btn``)
   * - Update Analysis button
     - ``button[data-testid="update-analysis-btn"]`` (also ``#update-analysis-btn``)
   * - Pull status message
     - ``[data-testid="pull-status"]`` (``data-poll="true"`` while running)
   * - Each analysis answer
     - ``[data-testid="analysis-answer"]``; its text starts with ``Answer:``

Percentages are checked with the regex ``(\d+(?:\.\d+)?)%``; each match
must fully match ``\d+\.\d{2}``.

Fixtures
--------

.. list-table::
   :header-rows: 1
   :widths: 24 30 46

   * - Fixture
     - Defined in
     - Provides
   * - ``flask_app_module``
     - ``test_buttons.py``, ``test_integration_end_to_end.py``
     - The imported ``app`` module, with its pull state reset (and, for
       integration, the test rows cleaned up)
   * - ``app``
     - ``test_flask_page.py``, ``test_buttons.py``,
       ``test_analysis_format.py``, ``test_integration_end_to_end.py``
     - ``create_app({"TESTING": True})``; the data layer is stubbed unless
       the test is an integration test
   * - ``client``
     - same files as ``app``
     - ``app.test_client()`` -- no browser is used
   * - ``page_html``
     - ``test_analysis_format.py``
     - The rendered HTML of ``GET /analysis`` with canned query results
   * - ``idle_state`` (autouse)
     - ``test_app_internals.py``
     - Resets ``_pull_state`` to idle before each test
   * - ``db_session``
     - ``test_db_insert.py``
     - A real SQLAlchemy session; creates the table if needed, deletes
       test rows afterwards, and **skips** if PostgreSQL is unreachable
   * - ``fake_connect``
     - ``test_load_data.py``, ``test_query_data.py``
     - Replaces ``psycopg.connect`` with a fake connection that records
       queries, commits, rollbacks and closes
   * - ``no_real_model`` (autouse)
     - ``test_llm_standardize.py``
     - Replaces model download/loading with fakes so no LLM is loaded
   * - ``fake_call_llm``
     - ``test_llm_standardize.py``
     - A deterministic stand-in for ``llm_standardize._call_llm``

Test doubles
------------

No test reaches the real GradCafe site, downloads a model, or waits on a
real scrape. Doubles are injected with ``monkeypatch``:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Double
     - Replaces / purpose
   * - ``_FakeSessionCtx``
     - ``models.get_session()`` in web/button/analysis tests, so pages render
       without a database
   * - Stubbed ``app.q.*``
     - Every ``orm_queries`` function returns fixed values, which makes
       formatting assertions deterministic
   * - ``_FakePullProcess`` / ``_FakeScraperProcess`` / ``_FakeProc``
     - ``subprocess.Popen`` in :func:`app.start_pull_data`. They yield canned
       output ending in ``PULL_DATA_SUMMARY::{...}`` and a chosen exit code.
       ``_FakeScraperProcess`` is the fake scraper: it loads its records into
       the test database with ``pull_data``'s real loader.
       ``_ExplodingProc`` makes the watcher itself fail.
   * - ``_FakeConnection`` / ``_FakeCursor``
     - ``psycopg.connect`` for ``load_data.py`` and ``query_data.py``
   * - ``_Response`` / ``_Headers``
     - ``urllib.request.urlopen`` in ``test_scrape.py``; ``scrape.time.sleep``
       is replaced with a list so no real delay happens
   * - ``fake_scrape`` / ``fake_clean`` / ``_FakeSession``
     - Each stage of ``pull_data.main`` in ``test_pull_data.py``
   * - ``_FakeSession`` (``test_orm_queries.py``)
     - Compiles every statement with the real PostgreSQL dialect and returns
       canned rows, so a malformed query still fails
   * - ``_FakeLlama`` / ``_FakeExecutor``
     - ``llama_cpp.Llama`` and ``ProcessPoolExecutor`` (run serially
       in-process)

Waiting for a pull without ``sleep()``
--------------------------------------

A pull finishes on a background watcher thread. Instead of polling with
``time.sleep()``, tests call :func:`app.wait_for_pull`, which joins that
thread and returns ``True`` once the app is idle:

.. code-block:: python

   pytestmark = pytest.mark.buttons

   def test_failed_pull_is_reported(flask_app_module, client, monkeypatch):
       monkeypatch.setattr(flask_app_module.subprocess, "Popen",
                           lambda *a, **k: _FakePullProcess(["boom"], returncode=1))
       assert client.post("/pull-data").status_code == 200
       assert flask_app_module.wait_for_pull(5)
       assert client.get("/pull-data/status").get_json()["success"] is False

Busy state is set directly (``_pull_state["running"] = True``), so the 409
paths are tested without starting a thread.

Error-path tests
----------------

* ``TestPullDataErrors`` -- the loader can't start: ``POST /pull-data``
  returns 500 ``{"ok": false}`` and the app is not left busy. A pull that
  exits non-zero is reported with ``success: false``.
* ``TestNoPartialWrites`` -- one invalid row in a batch raises an
  ``IntegrityError``; after rollback none of that batch's rows exist.
* ``test_database_failure_returns_1`` -- ``pull_data.main`` returns 1
  and never commits when the database is down.

Database for db / integration tests
-----------------------------------

Point ``DATABASE_URL`` at a **test** database before running the suite:

.. code-block:: bash

   export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/gradcafe_test"

Test rows use a dedicated ``https://test.invalid/...`` URL prefix and are
deleted afterwards, but a separate database is still safer. In GitHub
Actions, ``.github/workflows/tests.yml`` starts a ``postgres`` service
container and exports the same variable.
