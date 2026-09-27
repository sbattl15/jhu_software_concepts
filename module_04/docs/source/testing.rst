Testing Guide
=============

The suite lives in ``module_04/tests`` and uses pytest with pytest-cov.
``pytest.ini`` puts ``src/flask_website`` on the import path, registers the
markers and enforces **100 % coverage** of ``src/flask_website``:

.. code-block:: ini

   [pytest]
   pythonpath = src/flask_website
   addopts = -q --cov=src/flask_website --cov-report=term-missing --cov-fail-under=100
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
   pytest -m "web or buttons or analysis or db or integration" # every marked test
   pytest -m web                                               # page rendering only
   pytest -m buttons                                           # button + pipeline tests
   pytest -m "not db and not integration"                      # no database needed

Every test file sets a module-level ``pytestmark``, so each test has a
marker:

.. list-table::
   :header-rows: 1
   :widths: 16 34 50

   * - Marker
     - Files
     - What is checked
   * - ``web``
     - ``test_flask_page.py``
     - ``GET /`` returns 200, the page has both buttons, the analysis
       headings and ``Answer:`` labels.
   * - ``buttons``
     - ``test_buttons.py``, ``test_app_internals.py``, ``test_pull_data.py``,
       ``test_scrape.py``, ``test_clean.py``, ``test_llm_standardize.py``
     - ``POST /pull-data`` returns 200 and starts a pull, or 409 if one is
       running. ``POST /update-analysis`` returns 200 when idle and 409 when
       busy. Also every stage the Pull Data button runs.
   * - ``analysis``
     - ``test_analysis_format.py`` (plus ``TestTrendLabels``)
     - Answer labels are present, and every percentage is shown with two
       decimals.
   * - ``db``
     - ``test_db_insert.py``, ``test_orm_queries.py`` (plus ``TestModels``)
     - Rows are inserted by a pull, required fields are non-null,
       re-pulling is idempotent (no duplicate ``url`` rows), and every ORM
       query compiles against the PostgreSQL dialect.
   * - ``integration``
     - ``test_integration_end_to_end.py``
     - Pull Data → Update Analysis → ``GET /`` shows the new rows; repeated
       pulls don't duplicate.

Expected selectors
------------------

``templates/index.html`` has stable ``id`` attributes that tests and the
page's JavaScript rely on. Don't rename them without updating the tests.

.. list-table::
   :header-rows: 1

   * - Element
     - Selector
   * - Pull Data button
     - ``#pull-data-btn`` (disabled while a pull runs)
   * - Update Analysis button
     - ``#update-analysis-btn``
   * - Pull status message
     - ``#pull-status`` (``data-poll="true"`` while running)
   * - Required results section
     - ``#required-heading``
   * - Original questions section
     - ``#original-heading``
   * - Each answer
     - Text beginning with ``Answer:``

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
     - The imported ``app`` module, with its pull state reset
   * - ``app``
     - ``test_flask_page.py``, ``test_buttons.py``,
       ``test_analysis_format.py``, ``test_integration_end_to_end.py``
     - A testable Flask app with real routes and ``TESTING=True``; the
       data layer is stubbed unless the test is an integration test
   * - ``client``
     - same files as ``app``
     - ``app.test_client()``
   * - ``page_html``
     - ``test_analysis_format.py``
     - The rendered HTML of ``GET /`` with canned query results
   * - ``idle_state`` (autouse)
     - ``test_app_internals.py``
     - Resets ``_pull_state`` to idle before each test
   * - ``db_session``
     - ``test_db_insert.py``
     - A real SQLAlchemy session; creates the table if needed, deletes
       test rows afterwards, and **skips** if Postgres is unreachable
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
       ``_ExplodingProc`` makes the watcher itself fail.
   * - ``_Response`` / ``_Headers``
     - ``urllib.request.urlopen`` in ``test_scrape.py``; ``scrape.time.sleep``
       is patched out too
   * - ``fake_scrape`` / ``fake_clean`` / ``_FakeSession``
     - Each stage of ``pull_data.main`` in ``test_pull_data.py``
   * - ``_FakeSession`` (``test_orm_queries.py``)
     - Compiles every statement with the real PostgreSQL dialect and returns
       canned rows, so a malformed query still fails
   * - ``_FakeLlama`` / ``_FakeExecutor``
     - ``llama_cpp.Llama`` and ``ProcessPoolExecutor`` (run serially
       in-process)

Example:

.. code-block:: python

   pytestmark = pytest.mark.buttons

   def test_update_blocked_while_pulling(client, flask_app_module):
       flask_app_module._pull_state["running"] = True
       resp = client.post("/update-analysis")
       assert resp.status_code == 409

Database for db / integration tests
-----------------------------------

Point the ``PG*`` variables at a **test** database before running these
tests. Test rows use a dedicated URL prefix and are deleted afterwards, but
a separate database is still safer. In GitHub Actions, run a
``postgres`` service container and export the same variables.
