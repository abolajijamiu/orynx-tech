# Orynx BookLeads

Extract book and author leads from publishing platforms — bibliographic APIs,
publisher catalogues, retailers and review sites — then deduplicate, score and
export them to a spreadsheet.

The point of the design is that **adding a platform is a YAML file, not a code
change**. Most publisher and retail book pages already publish `schema.org/Book`
markup, so the generic extractor often needs no selectors at all.

```
discover ─► fetch ─► extract ─► normalise ─► resolve ─► enrich ─► score ─► export
           (polite)  (JSON-LD   (ISBN,       (dedupe    (how to  (profiles) (CSV/
                     or CSS)     dates,       books &    reach              XLSX)
                                 names)       authors)   them)
```

## Quick start

```bash
make install                 # venv + dependencies
cp .env.example .env         # then edit ORYNX_USER_AGENT — see "Identify yourself"
echo 'ORYNX_DATABASE_URL=sqlite+pysqlite:///./orynx.db' >> .env   # or use Postgres

.venv/bin/orynx doctor       # config and reachability check — run this first
.venv/bin/orynx quickstart --query "self-published memoir"
```

`quickstart` sets up the database, searches Open Library and Google Books,
enriches the authors it finds, scores everything and writes a spreadsheet. Open it
and read fifty rows: would you pitch these people? That answer tells you more than
any amount of configuration.

For Postgres instead of SQLite, run `make db` and leave `ORYNX_DATABASE_URL` at
its default. Step by step, rather than `quickstart`:

```bash
.venv/bin/orynx init-db
.venv/bin/orynx run --source openlibrary --query "self-published memoir" --limit 200
.venv/bin/orynx enrich                 # find how to reach them
.venv/bin/orynx leads --limit 20
.venv/bin/orynx export --format xlsx --tier A --tier B
```

If a crawl returns nothing, run `orynx doctor` before anything else — it separates
a blocked network or bad config from a genuinely empty result.

## Sources

Two kinds, addressed identically by id.

**Built-in API adapters** (`src/orynx/sources/api/`) — hand-written, since each
API has its own shape:

| id | platform | key | why it earns its place |
|---|---|---|---|
| `openlibrary` | Open Library | none | Broadest coverage; ratings counts drive the visibility signal |
| `googlebooks` | Google Books | optional | Best publisher attribution, for imprint targeting |
| `crossref` | Crossref | none | Scholarly monographs with author affiliations |
| `openalex` | OpenAlex | none | Academic authors with institution and country |

**Recipe-driven sites** (`src/orynx/recipes/*.yaml`) — everything else. Four ship
as starting points: `pegasuspublishers`, `koehlerbooks`, `pacificbookreview`,
`readerdepot`.

> **The shipped site recipes have unverified selectors.** They were written
> without live access to those sites. The JSON-LD path works regardless if the
> site publishes schema.org markup; the CSS fallbacks are educated guesses.
> Run `orynx recipe test <id>` against each before trusting a full crawl.

### Adding a platform

```bash
# 1. Probe a book page; reports what schema.org markup it already exposes
orynx recipe scaffold https://somepress.com/books/a-title -o src/orynx/recipes/somepress.yaml

# 2. Check what comes back
orynx recipe test somepress --limit 3

# 3. Adjust selectors in the YAML if fields are missing, then crawl
orynx run --source somepress --limit 500
```

`src/orynx/recipes/_TEMPLATE.yaml` documents every option. A recipe covers three
things: how to find listing pages (`paginate`, `static`, or `sitemap`), how to
pull book links off them, and how to read a detail page.

## Scoring

A lead is an (author, book) pair with a 0–100 score, an A–D tier, and a
breakdown of which signals fired. Weights live in **profiles**, because a good
lead means different things to different businesses:

| profile | optimises for | favours |
|---|---|---|
| `services` (default) | editing, design, formatting | recent, author-funded, small catalogue |
| `marketing` | launch campaigns, PR, ads | recent or forthcoming, low visibility, contactable |
| `saas` | tool and marketplace signups | contactable above all |
| `rights` | translation, audio, film, catalogue | **inverts the above** — high visibility, trade-published |

```bash
orynx rescore --profile marketing     # re-score existing leads, no re-crawl
```

Signals: `recency`, `author_funded`, `small_catalogue`, `low_visibility`,
`high_visibility`, `trade_published`, `contactable`, `source_trust`,
`corroborated`. Edit `PROFILES` in `src/orynx/pipeline/score.py` to tune, or add
your own profile.

## Enrichment: how to actually reach them

Extraction yields names and titles, almost none of which are contactable. A lead
list you cannot email is not yet a lead list, so enrichment is a first-class stage
rather than an afterthought.

Three sources run as a chain, ordered so each hands the next a stronger
identifier than a name:

1. **Open Library author records** — often carry the author's own website in
   `links`, and a Wikidata Q-id in `remote_ids`.
2. **Wikidata** — official website, social handles and ORCID, keyless and open.
   Given a Q-id from step 1, the lookup is exact rather than a guess.
3. **The author's own site** — read for published contact details, but only once
   an earlier step has found its address.

```bash
orynx enrich                                              # authors with no contact yet
orynx enrich --all --limit 500
orynx run --source openlibrary --query "..." --contacts   # crawl and enrich together
```

**Guarding against the wrong person.** Searching Wikidata for a common name
returns footballers as readily as novelists, and attaching a stranger's Twitter to
a lead is worse than having no contact at all. Two defences apply: an identifier
handed over by Open Library is used directly with no guessing, and a name search
accepts a candidate only if it is a human whose occupations include writing —
checking *every* label match rather than the first, since the author is rarely
ranked top. Confidence is recorded accordingly (0.95 for an identifier lookup,
0.7 for a verified name search), and anything below 0.55 is discarded.

**Provenance is per fact, not per author.** A merged profile holds values from
several enrichers, so each contact records which one supplied it — the email from
the author's site, the ORCID from Wikidata — and each is separately deletable.

**Generic inboxes are judged by domain, not by wordlist.** `hello@` on the
author's own domain is how they ask to be reached; `info@` on a publisher's domain
is not theirs. The domain decides.

**Dead hosts are written off.** After three consecutive connection failures a host
is skipped for the rest of the run. Without this, one blocked identity source
costs a timeout per author — measured at roughly 17s each, or hours across a large
batch.

## Deduplication

The same book appears on many platforms under slightly different metadata.
Resolution runs in two stages — an exact key, then fuzzy matching *within a
block*, which keeps the cost linear rather than quadratic:

- **Books** match on ISBN-13 first (ISBN-10s are converted). Without an ISBN,
  the key is the folded title plus the first author's blocking key, then fuzzy
  title comparison within that author's books. A record arriving later *with* an
  ISBN merges into one stored without one, and upgrades its key.
- **Authors** match on a normalised name — accents folded, honorifics and
  suffixes stripped, `"King, Stephen"` rewritten to `"Stephen King"` — then
  fuzzy-matched within a `surname:initial` block at a 92 threshold, which accepts
  `Jon`/`John Smith` while keeping `Jane`/`Joan Smith` apart.

Merging fills gaps without overwriting, except ratings counts, where the highest
count seen wins — the platform reporting more ratings has the better view of a
book's real visibility.

## Compliance

This tool collects personal data about identifiable people, so the guardrails
are structural rather than advisory:

- **robots.txt is obeyed by default.** A recipe can override it only by also
  supplying `robots_override_reason` — the schema rejects it otherwise.
- **Rate limits are per registrable domain**, so two recipes pointing at one host
  cannot combine into double the traffic. A site's `Crawl-delay` is honoured when
  slower than your default.
- **Provenance on everything.** Every contact row records the URL it came from
  and when, so you can answer "why do you have my details?" and delete on request.
- **Suppression is enforced at export**, not at capture — an opt-out survives
  re-crawling, and every output path passes through the same check.
- **Generic inboxes are filtered.** `info@`, `press@` and similar belong to the
  site, not the author.
- **Contact discovery is opt-in** (`--contacts`) and only reads pages already
  linked from a book record. It never guesses addresses or probes mail servers.

```bash
orynx suppress add someone@example.com --kind email --reason "opted out"
orynx suppress add example.com --kind domain
orynx suppress add "Jane Doe" --kind author_name
orynx suppress list
```

**Identify yourself.** Set `ORYNX_USER_AGENT` to something that names you and
gives a contact route. A site owner who wants your crawler to stop needs a way to
reach you; the default is a placeholder and should not survive first contact with
a real site.

Whether a given site's terms permit automated access is a decision only you can
make. Set `permitted: false` on a recipe to keep it on file while excluding it
from every run — the reason stays recorded in the file rather than in someone's
memory. Sending unsolicited commercial email is separately governed by GDPR,
CAN-SPAM, PECR and their equivalents; a lawful basis for collection is not a
lawful basis for outreach.

## HTTP API

```bash
orynx serve                          # http://127.0.0.1:8000/docs
```

| method | path | purpose |
|---|---|---|
| `GET` | `/leads` | Filter by `min_score`, `tier`, `require_contact` |
| `GET` | `/leads/summary` | Counts by tier and status |
| `PATCH` | `/leads/{id}` | Update `status` and `notes` |
| `POST` | `/leads/export` | Download CSV or XLSX |
| `GET` | `/sources`, `/profiles` | What is available |
| `POST` | `/runs` | Start an ingest in the background |
| `GET` | `/runs` | Run history with per-source stats |

## Export

CSV (UTF-8 with BOM, so Excel opens it correctly) or XLSX with a formatted lead
sheet, tier colouring, frozen header, autofilter, and a Summary sheet counting
tiers, sources, contactability and suppressions.

```bash
orynx export --format xlsx --min-score 60 --require-contact --out leads.xlsx
```

## Layout

```
src/orynx/
  config.py, textutil.py     settings; ISBN/date/name helpers
  db/models.py               schema: raw -> entities -> scored leads
  fetch/                     the only HTTP path: robots, rate limits, cache, retries
  sources/
    base.py                  the RawBook contract every adapter implements
    api/                     hand-written API adapters
    html/                    recipe schema, extraction engine, generic source
    registry.py              unified lookup by id
  recipes/*.yaml             site definitions (start from _TEMPLATE.yaml)
  pipeline/                  normalise, dedupe, score, orchestrate
  enrich/                    author identity chain: Open Library, Wikidata, own site
  compliance/                suppression list
  export/                    row builder, CSV, XLSX
  api/                       FastAPI app
```

Adapters never touch `httpx` directly — everything goes through `PoliteClient`,
which is what makes politeness impossible to forget.

## Development

```bash
make test        # 138 tests, no network required
make lint
```

Tests run entirely against fixtures and `httpx.MockTransport`. Nothing in the
suite makes a live request, so it is safe to run in CI and against a fork.

## Status and next steps

Working today: four API adapters, the recipe engine (JSON-LD and CSS), dedupe,
the three-stage enrichment chain, four scoring profiles, suppression, CSV/XLSX
export, CLI and HTTP API.

**Not yet done: a single real crawl.** Everything is verified against fixtures, a
mock transport and a local demo site. Nothing here has run against the live Open
Library or Wikidata, because the environment it was built in blocks them. Your
first job is `orynx doctor`, then `orynx quickstart`, and the first thing to judge
is whether the leads are people you would actually pitch.

Worth building next, roughly in order of value:

1. **Verify the four shipped site recipes** against the live sites — one
   `orynx recipe test` run each. Those leads are only as good as their selectors,
   and the selectors are currently guesses.
2. **Tune the scoring weights** against a real export. The profiles are reasoned,
   not calibrated; fifty real rows will teach you more than the reasoning did.
3. **Scheduled re-crawls** with change detection — `raw_record.content_hash`
   already makes this cheap, and a new book by a known author is the highest
   intent signal in the business.
4. **Alembic migrations.** `init-db` uses `create_all`, which is fine until the
   schema changes under real data.
5. **Crowdfunding sources** (Kickstarter, Publishizer). Authors actively funding a
   book are the warmest leads in publishing; both need recipes rather than adapters.
6. **A review UI.** The API supports lead triage; there is no front end.
