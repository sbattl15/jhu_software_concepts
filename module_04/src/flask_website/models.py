"""SQLAlchemy model and database session for the ``applicants`` table.

The connection URL comes from the ``DATABASE_URL`` environment variable,
e.g. ``postgresql://user:password@localhost:5432/gradcafe_applications``.
If it isn't set, the URL is built from the standard ``PGUSER``,
``PGPASSWORD``, ``PGHOST``, ``PGPORT`` and ``PGDATABASE`` variables.
Tests and :func:`app.create_app` can point the app at a different database
with :func:`configure_database`.
"""

from __future__ import annotations

import os

from sqlalchemy import Column, Date, Float, Integer, Text, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()


class Applicant(Base):
    """One GradCafe applicant result (a row of the ``applicants`` table)."""

    __tablename__ = "applicants"

    p_id = Column(Integer, primary_key=True)                  # unique identifier (SERIAL PRIMARY KEY)
    program = Column(Text, nullable=False)                    # University and Department/Program
    comments = Column(Text)                                   # applicant comments
    date_added = Column(Date)                                 # date entry was added
    url = Column(Text, nullable=False, unique=True)            # link to Grad Cafe entry (natural key)
    status = Column(Text)                                     # admission status
    term = Column(Text)                                       # intended start term
    us_or_international = Column(Text)                        # applicant nationality classification
    gpa = Column(Float)                                       # applicant GPA
    gre = Column(Float)                                       # GRE Quantitative score
    gre_v = Column(Float)                                     # GRE Verbal score
    gre_aw = Column(Float)                                    # GRE Analytical Writing score
    degree = Column(Text)                                     # degree type
    llm_generated_program = Column(Text)                      # LLM-generated department/program
    llm_generated_university = Column(Text)                   # LLM-generated university

    def __repr__(self) -> str:
        """Short representation showing id, program, term and status."""
        return (
            f"Applicant(p_id={self.p_id!r}, program={self.program!r}, "
            f"term={self.term!r}, status={self.status!r})"
        )


# Engine / Session


def normalize_database_url(url: str) -> str:
    """Make a PostgreSQL URL use the installed psycopg (v3) driver.

    ``postgresql://`` and ``postgres://`` URLs would otherwise make
    SQLAlchemy look for psycopg2, which this project doesn't install.

    :param url: A database URL, e.g. ``postgresql://u:p@host:5432/db``.
    :returns: The same URL with the ``postgresql+psycopg://`` scheme.
    :rtype: str

    >>> normalize_database_url("postgres://u:p@h/db")
    'postgresql+psycopg://u:p@h/db'
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def database_url_from_env():
    """Return the database URL from ``DATABASE_URL``, or build one from ``PG*``.

    :returns: A URL string or :class:`sqlalchemy.engine.URL`.
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return normalize_database_url(env_url)
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "gradcafe_applications"),
    )


#: The URL the engine is currently connected to.
DATABASE_URL = database_url_from_env()

#: The SQLAlchemy engine (rebuilt by :func:`configure_database`).
engine = create_engine(DATABASE_URL)

# Bound session factory. Call SessionLocal() (or use get_session() below)
# to get a Session for querying/updating the applicants table.
SessionLocal = sessionmaker(bind=engine)


def configure_database(url) -> None:
    """Point the engine and every new session at a different database.

    Used by :func:`app.create_app` when a ``DATABASE_URL`` is passed in the
    config (for example a test database).

    :param url: A database URL string or :class:`sqlalchemy.engine.URL`.
    :rtype: None
    """
    global DATABASE_URL, engine
    if isinstance(url, str):
        url = normalize_database_url(url)
    DATABASE_URL = url
    engine = create_engine(url)
    SessionLocal.configure(bind=engine)


def get_session() -> Session:
    """Return a new SQLAlchemy session bound to the applicants database.

    :rtype: sqlalchemy.orm.Session
    """
    return SessionLocal()


if __name__ == "__main__":
    # Quick smoke test: confirms the Engine/Session can actually reach the
    # existing applicants table through this model, without modifying or
    # duplicating any data.
    with get_session() as session:
        count = session.query(Applicant).count()
        print(f"Applicant model connected -- {count:,} row(s) in 'applicants'.")
