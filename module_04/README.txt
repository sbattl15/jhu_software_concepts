Name: Shane Battles (sbattl15)
Module Info: Module 4 Assignment: Testing and Documentation

Documentation (Sphinx, hosted on Read the Docs):
https://<your-project-slug>.readthedocs.io/en/latest/

The documentation source is in module_04/docs/. To build it locally:
    pip install -r docs/requirements.txt
    cd docs
    make html        (Windows: .\make.bat html)
Then open docs/build/html/index.html.

Running the tests (from module_04):
    pytest
    pytest -m "web or buttons or analysis or db or integration"
The db and integration tests need the PGDATABASE, PGUSER, PGPASSWORD, PGHOST and PGPORT environment variables set to a reachable PostgreSQL database.