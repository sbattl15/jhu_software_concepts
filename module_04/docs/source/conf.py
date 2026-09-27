"""Sphinx configuration for the GradCafe Analytics (Module 4) documentation."""

import os
import sys

# Paths are relative to this file so the build works on any machine and on
# Read the Docs (never hard-code an absolute Windows path here).
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, os.path.join(SRC, "flask_website"))  # app, scrape, clean, pull_data, ...
sys.path.insert(1, SRC)                                  # load_data, query_data

# -- Project information -----------------------------------------------------

project = "GradCafe Analytics"
copyright = "2026, Shane Battles (sbattl15)"
author = "Shane Battles (sbattl15)"
release = "1.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",    # API pages from docstrings
    "sphinx.ext.viewcode",   # [source] links
    "sphinx.ext.napoleon",   # accept Google/NumPy style docstrings too
]

# Heavy or service-dependent packages are mocked so autodoc can import every
# module without PostgreSQL, a database driver, or the local LLM installed.
autodoc_mock_imports = [
    "psycopg",
    "sqlalchemy",
    "models",
    "orm_queries",
    "llama_cpp",
    "huggingface_hub",
]

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "show-inheritance": True,
}
autodoc_typehints = "description"

templates_path = ["_templates"]
exclude_patterns = ["_build", "build"]

# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_title = "GradCafe Analytics"
html_static_path = ["_static"]
