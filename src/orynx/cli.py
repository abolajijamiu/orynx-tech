"""Command line interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from orynx.config import get_settings
from orynx.db.base import get_engine, init_db, session_scope
from orynx.logging import setup_logging

app = typer.Typer(
    help="Extract book and author leads from publishing platforms.",
    no_args_is_help=True,
)
recipe_app = typer.Typer(help="Build and check site recipes.", no_args_is_help=True)
suppress_app = typer.Typer(help="Manage the do-not-contact list.", no_args_is_help=True)
app.add_typer(recipe_app, name="recipe")
app.add_typer(suppress_app, name="suppress")

console = Console()


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    setup_logging("DEBUG" if verbose else "INFO")


@app.command("init-db")
def init_db_command() -> None:
    """Create database tables."""
    init_db()
    console.print(f"[green]Schema ready[/] at {get_settings().database_url}")


@app.command("sources")
def list_sources() -> None:
    """List every available source, built-in or recipe-driven."""
    from orynx.sources.registry import get_registry

    table = Table(title="Sources", header_style="bold")
    for column in ("id", "name", "kind", "trust", "homepage"):
        table.add_column(column)
    for meta in get_registry().describe():
        table.add_row(meta.id, meta.name, meta.kind, f"{meta.trust:.2f}", meta.homepage or "-")
    console.print(table)


@app.command("run")
def run_command(
    source: list[str] = typer.Option(
        None, "--source", "-s", help="Source id; repeatable. Defaults to all enabled."
    ),
    query: str = typer.Option(None, "--query", "-q", help="Search term for API sources."),
    limit: int = typer.Option(200, "--limit", "-n", help="Max records per source."),
    profile: str = typer.Option("services", "--profile", "-p", help="Scoring profile."),
    contacts: bool = typer.Option(
        False, "--contacts",
        help="Also run the enrichment chain to find how to reach each author.",
    ),
) -> None:
    """Ingest from one or more sources, then score every lead."""
    from orynx.pipeline.run import run_pipeline
    from orynx.sources.registry import get_registry

    registry = get_registry()
    source_ids = list(source) if source else registry.ids()
    if not source_ids:
        console.print("[red]No sources available.[/]")
        raise typer.Exit(1)

    init_db()
    with session_scope() as session:
        stats = asyncio.run(
            run_pipeline(
                session, source_ids,
                query=query, limit=limit, profile=profile, with_contacts=contacts,
                registry=registry,
            )
        )

    table = Table(title="Ingest results", header_style="bold")
    for column in ("source", "fetched", "new", "dupes", "books", "authors", "contacts", "errors"):
        table.add_column(column)
    for stat in stats:
        table.add_row(
            stat.source_id, str(stat.fetched), str(stat.stored), str(stat.duplicates),
            str(stat.books), str(stat.authors), str(stat.contacts), str(len(stat.errors)),
        )
    console.print(table)
    console.print("Next: [bold]orynx export --format xlsx[/]")


@app.command("rescore")
def rescore_command(
    profile: str = typer.Option("services", "--profile", "-p", help="Scoring profile.")
) -> None:
    """Re-score existing leads under a different profile."""
    from orynx.pipeline.run import rebuild_leads

    with session_scope() as session:
        count = rebuild_leads(session, profile)
    console.print(f"[green]Rescored {count} leads[/] with profile '{profile}'.")


@app.command("leads")
def list_leads(
    limit: int = typer.Option(25, "--limit", "-n"),
    min_score: float = typer.Option(0.0, "--min-score"),
    tier: list[str] = typer.Option(None, "--tier", help="Filter by tier; repeatable."),
) -> None:
    """Show the top leads in the terminal."""
    from orynx.export.builder import build_rows

    with session_scope() as session:
        rows, suppressed = build_rows(
            session, min_score=min_score, tiers=list(tier) if tier else None, limit=limit
        )

    table = Table(title=f"Top {len(rows)} leads", header_style="bold")
    for column in ("score", "tier", "author", "book", "publisher", "year", "contact"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            f"{row.score:.1f}", row.tier, row.author_name, row.book_title[:44],
            (row.publisher or "-")[:24], row.published_year or "-",
            row.author_emails or row.author_website or "-",
        )
    console.print(table)
    if suppressed:
        console.print(f"[yellow]{suppressed} lead(s) withheld by the suppression list.[/]")


@app.command("export")
def export_command(
    fmt: str = typer.Option("xlsx", "--format", "-f", help="csv or xlsx."),
    out: Path = typer.Option(None, "--out", "-o", help="Output path."),
    min_score: float = typer.Option(0.0, "--min-score"),
    tier: list[str] = typer.Option(None, "--tier", help="Filter by tier; repeatable."),
    limit: int = typer.Option(None, "--limit", "-n"),
    require_contact: bool = typer.Option(
        False, "--require-contact", help="Only leads with an email, site or social."
    ),
) -> None:
    """Write leads to a spreadsheet."""
    from orynx.export.builder import build_rows
    from orynx.export.csv_export import write_csv
    from orynx.export.xlsx_export import write_xlsx

    fmt = fmt.lower()
    if fmt not in {"csv", "xlsx"}:
        console.print("[red]--format must be csv or xlsx[/]")
        raise typer.Exit(1)

    settings = get_settings()
    out = out or settings.export_dir / f"leads.{fmt}"

    with session_scope() as session:
        rows, suppressed = build_rows(
            session,
            min_score=min_score,
            tiers=list(tier) if tier else None,
            limit=limit,
            require_contact=require_contact,
        )

    if not rows:
        console.print("[yellow]No leads matched. Run 'orynx run' first.[/]")
        raise typer.Exit(0)

    path = write_csv(rows, out) if fmt == "csv" else write_xlsx(rows, out, suppressed=suppressed)
    console.print(f"[green]Wrote {len(rows)} leads[/] to {path}")
    if suppressed:
        console.print(f"[yellow]{suppressed} withheld by the suppression list.[/]")


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("orynx.api.app:app", host=host, port=port)


@app.command("enrich")
def enrich_command(
    limit: int = typer.Option(None, "--limit", "-n", help="Max authors to enrich."),
    all_authors: bool = typer.Option(
        False, "--all", help="Include authors that already have a contact point."
    ),
) -> None:
    """Find contact details for authors already in the database.

    Chains Open Library author records, Wikidata, and the author's own site.
    """
    from orynx.fetch import PoliteClient
    from orynx.pipeline.run import enrich_pending_authors

    async def _run():
        client = PoliteClient()
        try:
            with session_scope() as session:
                return await enrich_pending_authors(
                    session, client, limit=limit, only_missing_contacts=not all_authors
                )
        finally:
            await client.aclose()

    stats = asyncio.run(_run())
    table = Table(title="Enrichment", header_style="bold")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("authors attempted", str(stats.attempted))
    table.add_row("authors enriched", str(stats.enriched))
    table.add_row("contacts added", str(stats.contacts_added))
    table.add_row("websites found", str(stats.websites_found))
    table.add_row("emails found", str(stats.emails_found))
    for source, count in sorted(stats.by_source.items()):
        table.add_row(f"  via {source}", str(count))
    console.print(table)
    if stats.attempted and not stats.enriched:
        console.print(
            "[yellow]Nothing found.[/] Run 'orynx doctor' — the identity sources "
            "may be unreachable from this network."
        )


@app.command("doctor")
def doctor() -> None:
    """Check configuration and reachability before a real crawl.

    Run this first if a crawl returns nothing; it separates a network or config
    problem from an empty result.
    """
    import httpx

    from orynx.sources.registry import get_registry

    settings = get_settings()
    table = Table(title="Diagnostics", header_style="bold")
    for column in ("check", "result", "detail"):
        table.add_column(column)

    ok = "[green]ok[/]"
    warn = "[yellow]warn[/]"
    bad = "[red]fail[/]"

    # Database
    try:
        from sqlalchemy import text

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        table.add_row("database", ok, settings.database_url.split("@")[-1])
    except Exception as exc:
        table.add_row("database", bad, str(exc)[:70])

    # A default user agent means site owners cannot identify or contact you.
    if "example.com" in settings.user_agent:
        table.add_row("user agent", warn, "still the placeholder — set ORYNX_USER_AGENT")
    else:
        table.add_row("user agent", ok, settings.user_agent[:60])

    table.add_row(
        "contact email",
        ok if settings.contact_email else warn,
        settings.contact_email or "unset (raises rate limits on Crossref/OpenAlex)",
    )

    # Recipes
    try:
        registry = get_registry()
        table.add_row("recipes", ok, f"{len(registry.recipes)} loaded")
    except Exception as exc:
        table.add_row("recipes", bad, str(exc)[:70])

    # Reachability of every network dependency.
    probes = {
        "openlibrary": "https://openlibrary.org/search.json?q=test&limit=1",
        "googlebooks": "https://www.googleapis.com/books/v1/volumes?q=test&maxResults=1",
        "crossref": "https://api.crossref.org/works?rows=1",
        "openalex": "https://api.openalex.org/works?per-page=1",
        "wikidata": "https://www.wikidata.org/w/api.php?action=wbgetentities&ids=Q42&format=json",
    }
    for name, url in probes.items():
        try:
            response = httpx.get(
                url, timeout=15.0, headers={"User-Agent": settings.user_agent},
                follow_redirects=True,
            )
            if response.status_code == 200:
                table.add_row(f"reach {name}", ok, "200")
            elif response.status_code == 429:
                table.add_row(f"reach {name}", warn, "429 rate limited — try a key or wait")
            else:
                table.add_row(f"reach {name}", warn, f"HTTP {response.status_code}")
        except Exception as exc:
            table.add_row(f"reach {name}", bad, type(exc).__name__)

    console.print(table)


@app.command("quickstart")
def quickstart(
    query: str = typer.Option(
        "self-published memoir", "--query", "-q", help="What to search for."
    ),
    limit: int = typer.Option(200, "--limit", "-n"),
    profile: str = typer.Option("services", "--profile", "-p"),
    contacts: bool = typer.Option(True, "--contacts/--no-contacts"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Set up, crawl, enrich and export in one command.

    The fastest way to find out whether the leads are any good.
    """
    from orynx.export.builder import build_rows
    from orynx.export.xlsx_export import write_xlsx
    from orynx.pipeline.run import run_pipeline

    console.print("[bold]1/4[/] preparing database")
    init_db()

    console.print(f"[bold]2/4[/] searching Open Library and Google Books for {query!r}")
    with session_scope() as session:
        stats = asyncio.run(
            run_pipeline(
                session, ["openlibrary", "googlebooks"],
                query=query, limit=limit, profile=profile, with_contacts=contacts,
            )
        )
        fetched = sum(s.fetched for s in stats)
        if fetched == 0:
            console.print(
                "[red]Nothing was fetched.[/] Run [bold]orynx doctor[/] to find out why."
            )
            raise typer.Exit(1)

        console.print(f"[bold]3/4[/] scoring leads ({fetched} records fetched)")
        rows, suppressed = build_rows(session)

        console.print("[bold]4/4[/] exporting")
        path = out or get_settings().export_dir / "leads.xlsx"
        write_xlsx(rows, path, suppressed=suppressed)

    reachable = sum(1 for r in rows if r.author_emails or r.author_website or r.author_socials)
    summary = Table(title="Quickstart result", header_style="bold")
    summary.add_column("metric")
    summary.add_column("value", justify="right")
    summary.add_row("leads", str(len(rows)))
    summary.add_row("tier A", str(sum(1 for r in rows if r.tier == "A")))
    summary.add_row("tier B", str(sum(1 for r in rows if r.tier == "B")))
    summary.add_row("with a contact point", str(reachable))
    summary.add_row("with an email", str(sum(1 for r in rows if r.author_emails)))
    console.print(summary)
    console.print(f"\n[green]Open {path}[/] and read 50 rows. Would you pitch these people?")
    console.print("If they skew wrong, try: [bold]orynx rescore --profile marketing[/]")


# --------------------------------------------------------------------------- #
# Recipes
# --------------------------------------------------------------------------- #

@recipe_app.command("list")
def recipe_list() -> None:
    """Show installed recipes."""
    from orynx.sources.registry import get_registry

    registry = get_registry()
    table = Table(title=f"Recipes in {registry.recipe_dir}", header_style="bold")
    for column in ("id", "name", "kind", "enabled", "permitted", "strategy"):
        table.add_column(column)
    for recipe in registry.recipes.values():
        table.add_row(
            recipe.id, recipe.name, recipe.kind,
            "yes" if recipe.enabled else "no",
            "yes" if recipe.permitted else "NO",
            recipe.discover.strategy,
        )
    console.print(table)


@recipe_app.command("validate")
def recipe_validate(path: Path = typer.Argument(None, help="A single file; default all.")) -> None:
    """Check recipe files parse and satisfy the schema."""
    from orynx.sources.html.recipe import load_recipe

    settings = get_settings()
    paths = [path] if path else sorted(Path(settings.recipe_dir).glob("*.yaml"))
    failures = 0
    for candidate in paths:
        if candidate.stem.startswith("_"):
            continue
        try:
            load_recipe(candidate)
            console.print(f"[green]ok[/]   {candidate.name}")
        except Exception as exc:
            failures += 1
            console.print(f"[red]fail[/] {candidate.name}: {exc}")
    if failures:
        raise typer.Exit(1)


@recipe_app.command("test")
def recipe_test(
    source_id: str = typer.Argument(..., help="Recipe id to exercise."),
    limit: int = typer.Option(3, "--limit", "-n", help="Books to fetch."),
) -> None:
    """Run a recipe against the live site and print what it extracted.

    Use this to confirm selectors before a full crawl.
    """
    from orynx.fetch import PoliteClient
    from orynx.sources.registry import get_registry

    async def _run() -> list:
        client = PoliteClient()
        try:
            source = get_registry().build(source_id, client)
            return [book async for book in source.crawl(limit=limit)]
        finally:
            await client.aclose()

    books = asyncio.run(_run())
    if not books:
        console.print(
            "[yellow]No books extracted.[/] Check discover URLs and selectors, "
            "and confirm robots.txt allows these paths."
        )
        raise typer.Exit(1)

    for book in books:
        console.print(f"\n[bold]{book.title}[/]")
        console.print(f"  authors:   {[a.name for a in book.authors] or '[red]none[/]'}")
        console.print(f"  isbn13:    {book.isbn13 or '-'}")
        console.print(f"  published: {book.published_date or '-'}")
        console.print(f"  publisher: {book.publisher or '-'}")
        console.print(f"  url:       {book.url}")
    console.print(f"\n[green]{len(books)} book(s) extracted.[/]")


@recipe_app.command("scaffold")
def recipe_scaffold(
    url: str = typer.Argument(..., help="A book detail page on the target site."),
    out: Path = typer.Option(None, "--out", "-o", help="Write the recipe here."),
) -> None:
    """Probe a book page and print a starting recipe.

    Sites publishing schema.org Book markup need almost no selectors, so this
    reports what the generic JSON-LD path already found.
    """
    from urllib.parse import urlsplit

    from orynx.fetch import PoliteClient
    from orynx.sources.html.extract import book_fields_from_jsonld, find_book_jsonld

    async def _probe() -> tuple[int, str]:
        client = PoliteClient()
        try:
            result = await client.get(url)
            return result.status, result.text
        finally:
            await client.aclose()

    status, html = asyncio.run(_probe())
    if status >= 400:
        console.print(f"[red]Fetch returned {status}[/]")
        raise typer.Exit(1)

    jsonld = find_book_jsonld(html)
    parts = urlsplit(url)
    site_id = parts.netloc.replace("www.", "").split(".")[0]

    if jsonld:
        fields = book_fields_from_jsonld(jsonld)
        console.print("[green]Found schema.org Book markup.[/] Extracted:")
        console.print(json.dumps({k: v for k, v in fields.items() if v}, indent=2, default=str))
        detail_block = "  prefer_jsonld: true\n  fields: {}"
    else:
        console.print(
            "[yellow]No schema.org Book markup found.[/] "
            "You will need CSS selectors in the detail.fields block below."
        )
        detail_block = (
            "  prefer_jsonld: true\n"
            "  fields:\n"
            '    title: { css: "h1" }\n'
            '    authors: { css: ".author", many: true }\n'
            '    isbn: { css: "[itemprop=isbn]", transform: [isbn] }'
        )

    recipe = f"""id: {site_id}
name: {site_id.title()}
kind: publisher
homepage: {parts.scheme}://{parts.netloc}
trust: 0.5
enabled: true
permitted: true
permitted_note: "Scaffolded from {url}"

politeness:
  rate_limit_rps: 0.3
  obey_robots: true

discover:
  strategy: sitemap
  sitemap_url: "{parts.scheme}://{parts.netloc}/sitemap.xml"
  url_pattern: "{'/' + parts.path.strip('/').split('/')[0] + '/' if parts.path.strip('/') else '/'}"
  sitemap_yields: detail
  max_urls: 1000

detail:
  enabled: true
{detail_block}
"""
    console.print("\n[bold]Starting recipe:[/]\n")
    console.print(recipe)
    if out:
        out.write_text(recipe, encoding="utf-8")
        console.print(f"[green]Written to {out}[/]  Now run: orynx recipe test {site_id}")


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #

@suppress_app.command("add")
def suppress_add(
    value: str = typer.Argument(..., help="Email, domain, or author name."),
    kind: str = typer.Option("email", "--kind", "-k", help="email | domain | author_name"),
    reason: str = typer.Option(None, "--reason", "-r"),
) -> None:
    """Add an entry to the do-not-contact list."""
    from orynx.compliance.suppression import add_suppression

    if kind not in {"email", "domain", "author_name"}:
        console.print("[red]--kind must be email, domain or author_name[/]")
        raise typer.Exit(1)
    with session_scope() as session:
        add_suppression(session, kind, value, reason)
    console.print(f"[green]Suppressed[/] {kind}={value}")


@suppress_app.command("list")
def suppress_list() -> None:
    """Show the do-not-contact list."""
    from sqlalchemy import select

    from orynx.db.models import Suppression

    with session_scope() as session:
        rows = session.scalars(select(Suppression)).all()
        table = Table(title="Suppression list", header_style="bold")
        for column in ("kind", "value", "reason", "added"):
            table.add_column(column)
        for row in rows:
            table.add_row(row.kind, row.value, row.reason or "-", str(row.created_at)[:10])
    console.print(table)


@app.command("stats")
def stats_command() -> None:
    """Summarise what is currently in the database."""
    from sqlalchemy import func, select

    from orynx.db.models import Author, AuthorContact, Book, Lead, RawRecord

    with session_scope() as session:
        table = Table(title="Database", header_style="bold")
        table.add_column("entity")
        table.add_column("count", justify="right")
        for label, model in (
            ("raw records", RawRecord), ("books", Book),
            ("authors", Author), ("contacts", AuthorContact), ("leads", Lead),
        ):
            table.add_row(label, str(session.scalar(select(func.count()).select_from(model))))
        for tier in ("A", "B", "C", "D"):
            count = session.scalar(
                select(func.count()).select_from(Lead).where(Lead.tier == tier)
            )
            table.add_row(f"  tier {tier}", str(count))
    console.print(table)


if __name__ == "__main__":
    app()
