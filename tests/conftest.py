from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from orynx.config import Settings
from orynx.db.base import Base, get_engine
from orynx.fetch import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        user_agent="OrynxTest/0.1 (+https://example.test/bot)",
        contact_email="test@example.test",
        default_rate_limit_rps=1000.0,  # no real waiting in tests
        request_timeout=5.0,
        max_retries=1,
        obey_robots=False,
        cache_dir=tmp_path / "cache",
        export_dir=tmp_path / "exports",
        recipe_dir=Path(__file__).resolve().parents[1] / "src" / "orynx" / "recipes",
    )


@pytest.fixture
def session() -> Session:
    engine = get_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    db = maker()
    try:
        yield db
    finally:
        db.close()


def fixture_text(*parts: str) -> str:
    return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")


def fixture_json(*parts: str) -> dict:
    return json.loads(fixture_text(*parts))


def make_client(settings: Settings, routes: dict[str, tuple[int, str]]) -> PoliteClient:
    """Build a PoliteClient backed by a routing table instead of the network.

    Keys are matched as substrings of the request URL, longest first, so a test
    can stub a whole site with a handful of entries.
    """
    ordered = sorted(routes.items(), key=lambda kv: len(kv[0]), reverse=True)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, (status, body) in ordered:
            if pattern in url:
                return httpx.Response(status, text=body)
        return httpx.Response(404, text="not found")

    return PoliteClient(
        settings=settings,
        use_cache=False,
        obey_robots=False,
        transport=httpx.MockTransport(handler),
    )
