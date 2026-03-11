from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

Base = declarative_base()

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session, future=True)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


@contextmanager
def db_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
