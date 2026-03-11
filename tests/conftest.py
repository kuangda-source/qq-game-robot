from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.cache import MemoryCache
from app.config import Settings
from app.database import Base


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/15",
        qq_bot_name="机器人",
        steam_base_url="https://store.steampowered.com",
        openai_api_key=None,
    )


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(bind=engine, class_=Session, autocommit=False, autoflush=False, future=True)

    @contextmanager
    def _session_factory():
        session = local_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    yield _session_factory
    engine.dispose()


@pytest.fixture
def memory_cache() -> MemoryCache:
    return MemoryCache()
