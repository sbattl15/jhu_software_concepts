GradCafe Analytics
==================

GradCafe Analytics scrapes graduate-admissions results from
`The GradCafe <https://www.thegradcafe.com/survey>`_, cleans and
standardizes them, loads them into PostgreSQL, and serves an analysis
dashboard built with Flask.

.. code-block:: text

   scrape.py ──► clean.py ──► llm_standardize.py ──► load_data.py / pull_data.py ──► PostgreSQL
                                                                                      │
                                                  query_data.py / app.py (Flask)  ◄───┘

.. toctree::
   :maxdepth: 2
   :caption: Guide

   overview
   architecture
   testing
   operational

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/index

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
