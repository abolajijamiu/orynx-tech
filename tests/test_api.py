"""HTTP API behaviour, against a temporary SQLite database."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import orynx.api.deps as deps
from orynx.api.app import app
from orynx.db.base import Base, get_engine
from orynx.db.models import Author, Book, BookAuthor, BookSource, Lead, Source


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite+pysqlite:///{tmp_path/'api.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        db = maker()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(deps, "get_sessionmaker", lambda: maker)
    app.dependency_overrides[deps.get_session] = override

    db = maker()
    db.add(Source(id="fake", name="Fake", kind="hybrid", trust=0.7))
    author = Author(display_name="Amara Nwosu", normalized_name="amara nwosu",
                    dedupe_key="nwosu:a", website="https://amara.example")
    book = Book(title="The Quiet Harbour", isbn13="9781234567897", publisher="Koehler Books",
                published_on=date(2026, 3, 15), published_year=2026, dedupe_key="isbn:x")
    db.add_all([author, book])
    db.flush()
    db.add_all([
        BookAuthor(book_id=book.id, author_id=author.id),
        BookSource(book_id=book.id, source_id="fake", url="https://press.test/a"),
        Lead(author_id=author.id, book_id=book.id, score=88.0, tier="A",
             reasons=[{"signal": "recency", "points": 28.0}]),
    ])
    db.commit()
    db.close()

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_sources_endpoint_lists_adapters_and_recipes(client):
    ids = {s["id"] for s in client.get("/sources").json()["sources"]}
    assert {"openlibrary", "googlebooks"} <= ids


def test_profiles_endpoint_exposes_weights(client):
    profiles = client.get("/profiles").json()
    assert "services" in profiles
    assert profiles["services"]["weights"]["recency"] > 0


def test_leads_endpoint_returns_scored_rows(client):
    payload = client.get("/leads").json()
    assert payload["count"] == 1
    assert payload["leads"][0]["author_name"] == "Amara Nwosu"
    assert payload["leads"][0]["tier"] == "A"


def test_leads_min_score_filter(client):
    assert client.get("/leads", params={"min_score": 95}).json()["count"] == 0


def test_leads_tier_filter(client):
    assert client.get("/leads", params={"tier": ["D"]}).json()["count"] == 0
    assert client.get("/leads", params={"tier": ["A"]}).json()["count"] == 1


def test_summary_endpoint(client):
    summary = client.get("/leads/summary").json()
    assert summary["total"] == 1
    assert summary["by_tier"]["A"] == 1


def test_patch_updates_status_and_notes(client):
    response = client.patch("/leads/1", params={"status": "contacted", "notes": "emailed"})
    assert response.status_code == 200
    assert response.json()["status"] == "contacted"
    assert client.get("/leads/summary").json()["by_status"]["contacted"] == 1


def test_patch_rejects_an_invalid_status(client):
    assert client.patch("/leads/1", params={"status": "bogus"}).status_code == 422


def test_patch_unknown_lead_is_404(client):
    assert client.patch("/leads/9999", params={"status": "contacted"}).status_code == 404


def test_export_endpoint_returns_a_csv(client):
    response = client.post("/leads/export", params={"fmt": "csv"})
    assert response.status_code == 200
    assert "author_name" in response.text


def test_export_endpoint_404s_when_nothing_matches(client):
    assert client.post("/leads/export", params={"fmt": "csv", "min_score": 99}).status_code == 404


def test_run_endpoint_rejects_unknown_sources(client):
    response = client.post("/runs", json={"sources": ["nope"]})
    assert response.status_code == 422
    assert "unknown sources" in response.json()["detail"]


def test_run_endpoint_rejects_unknown_profile(client):
    response = client.post("/runs", json={"sources": ["openlibrary"], "profile": "nope"})
    assert response.status_code == 422


def test_runs_listing_is_empty_initially(client):
    assert client.get("/runs").json()["runs"] == []
