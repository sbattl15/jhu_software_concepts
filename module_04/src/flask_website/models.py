from __future__ import annotations

import os

from sqlalchemy import Column, Date, Float, Integer, Text, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()


class Applicant(Base):
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
        return (
            f"Applicant(p_id={self.p_id!r}, program={self.program!r}, "
            f"term={self.term!r}, status={self.status!r})"
        )


# Engine / Session
DATABASE_URL = URL.create(
    "postgresql+psycopg",
    username=os.environ.get("PGUSER", "postgres"),
    password=os.environ.get("PGPASSWORD"),
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", "5432")),
    database=os.environ.get("PGDATABASE", "gradcafe_applications"),
)

engine = create_engine(DATABASE_URL)

# Bound session factory. Call SessionLocal() (or use get_session() below)
# to get a Session for querying/updating the applicants table.
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Session:
    """Returns a new SQLAlchemy Session bound to the applicants database."""
    return SessionLocal()


if __name__ == "__main__":
    # Quick smoke test: confirms the Engine/Session can actually reach the
    # existing applicants table through this model, without modifying or
    # duplicating any data.
    with get_session() as session:
        count = session.query(Applicant).count()
        print(f"Applicant model connected -- {count:,} row(s) in 'applicants'.")
