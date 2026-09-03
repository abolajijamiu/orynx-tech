# Orynx Book Lead Extractor (browser extension)

Extracts book, author and contact details from **any** page you open — including
a site nobody has written a config for. It reads generic structure rather than
per-site rules, so a site you find tomorrow works immediately.

## Install (development)

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. **Load unpacked** → select this `extension/` folder
4. Visit any book page. A badge appears bottom-right with the number found.

Works in Chrome, Edge, Brave and any Chromium browser. Firefox needs a manifest
tweak (`background.scripts` instead of `service_worker`).

## Why an extension rather than a crawler

A server-side crawler fights the web: JavaScript-rendered pages, Cloudflare,
login walls, and per-site rules that break whenever a site is redesigned. In the
browser, the page is already rendered, already authenticated and already past bot
detection. It also generalises, because it reads what the page declares about
itself instead of where a config says to look.

The trade-off is reach: an extension only sees pages you actually open. Bulk
harvesting still belongs to the Python crawler in this repository. They are
complements — extension for discovery, difficult sites and one-off pages; crawler
for volume on sites that permit it.

## How extraction works

Four strategies, tried in descending order of reliability and then merged, so a
page with partial JSON-LD still gains a review count found only in the DOM:

1. **JSON-LD** (`schema.org` Book, Product, Review) — publishers and retailers
   publish this for search engines. Best quality, no configuration.
2. **Microdata** — `itemtype="…/Book"` subtrees.
3. **Meta tags** — Open Graph `book:*`, Dublin Core, `citation_*`.
4. **DOM heuristics** — last resort, and context-aware. On a page identified as
   a book platform, or simply shaped like a book catalogue, a cover plus an
   author-shaped line is evidence enough. Elsewhere hard evidence is required,
   so a consultancy's team page does not become a shelf of books.

Cover-only grids are handled too: where the title is drawn into the artwork and
the card holds no readable text, it is read from the image's `alt`.

Every field records which strategy produced it, in `extractedBy`.

### Byline parsing

The hardest part of the heuristic path. Two rules do the work: a name token must
end in a lowercase letter, so `Blake` is a name and `ISBN` is an acronym that
terminates the match; and common field labels (`Published`, `Price`, `Format`)
are excluded outright, because they otherwise look exactly like surnames.
Lowercase particles are preserved, so `Ngugi wa Thiongo` and
`Ludwig van Beethoven` survive intact.

## Contacts

Email, phone, WhatsApp (`wa.me` links and labelled numbers), LinkedIn,
Instagram, Twitter/X, Facebook and more, plus the contact or submissions page.

A generic inbox is judged by **domain, not by wordlist**: `hello@` on the site's
own domain is how they ask to be reached, while `info@` belonging to some other
company is noise. The site's own domain is taken from its canonical URL, so a
page served from a staging host or CDN still recognises its own addresses.

Nothing is guessed. No address patterns are invented and no mail servers are
probed — everything comes from what the page publishes about itself.

## The two views

The badge and panel on the page show **this page**. The toolbar popup has two
tabs: **This page** (live, with a Save button) and **Saved** (everything kept
across sites). It opens on whichever has something in it.

Rescan re-runs extraction and reports the count. Cover-only grids and
late-rendering catalogues both need it occasionally; the panel also rescans by
itself for about a minute after load, since most shop front-ends render their
grid after the page appears.

## Visiting every book on a listing

A listing gives titles and links; the detail pages hold the data worth having.
**Copy links** puts every same-site book link on the clipboard. **Visit each book
and save** works through them: one background tab at a time, read, saved, closed,
with a pause before the next, and a progress line you can stop at any point.

One tab at a time is deliberate. Opening forty at once is faster for about ten
seconds and then bogs the browser down and gets the session rate-limited; a paced
single worker looks like someone reading, and it can be interrupted.

Re-visiting a book already in the library does not duplicate it — the detail page
fills in the blanks on the row the listing created.

## The author hop

**Authors** works through the library rather than the current page: one visit per
author, however many of their books you have saved. It reads the author's page —
name, biography, own website, published address, socials, where they were born,
what they write, how many books — and writes all of it onto every book credited
to them, filling gaps rather than overwriting.

Where the author names their own website, that is opened too. This is the step
that actually produces addresses: a catalogue almost never publishes one, and an
author's own contact page usually does. It is skipped when an address was already
found, and never follows a social profile in place of a site.

Run it after visiting book pages, since that is where author links come from.

## What a detail page yields

Beyond the basics: original title, edition, series, awards, expected publication,
genres, page count, language, more-editions link, and the counts a page publishes
about itself — ratings, reviews, voters, views, want-to-read, currently reading,
editions.

For the author: the "about the author" text, their own website (social hosts are
excluded, so it is genuinely theirs), any published email, and Facebook,
Instagram, TikTok, X, LinkedIn, YouTube and Substack. Plus a capped sample of
community review text, trimmed so a spreadsheet cell stays usable.

`readersCount` and `viewsCount` stay empty on sites that publish neither, which
is most of them.

## What one run does

**Visit each book and save** now runs the whole chain without a second click:

```
listing page
  └─ each book page      title, description, ratings, reviews, dates, pages,
     │                   genres, original title, edition, language, editions,
     │                   statistics, review content
     └─ the author's page     name, bio, socials, birthplace, genres, own site
        └─ their own website  address, phone, further profiles
           └─ its contact page   where the homepage carries no address
```

The status line says **Working** while a run is live, with the count and the page
being read, and **Done** when it finishes — or **Stopped** if you stopped it. It
is also polled rather than only pushed, so opening the panel mid-run shows the
run rather than nothing: a broadcast sent before the panel existed is lost, and
that is exactly when someone goes looking for progress.

One background tab at a time throughout. Each author is visited once however
many of their books you hold, and an author already read is skipped on a later
run.

Where a book links straight to the author's own website rather than a profile
page — common in schema.org markup — that is recognised and harvested for
contacts instead.

**Social platforms are not opened.** Facebook, Instagram, TikTok, X and LinkedIn
sit behind login walls with active bot detection; automated access there risks
the account doing it being suspended, and phone numbers are effectively never
public on them. Profile URLs are captured wherever a page publishes them, which
is what an enrichment pass actually needs.

## The CSV, and the AI pass after it

32 columns, one row per book, in a fixed order: title, description, rating,
reviews, voters, views, dates, pages, genres, original title, edition, language,
more editions, statistics, **the stores it is available on**, then the author — name, biography, email, phone,
website, Facebook, Instagram, TikTok, X, LinkedIn, other profiles — then the
community review text, then the book and author page URLs.

Two are deliberately blank: **`found_email`** and **`outreach_message`**, for a
later pass to fill from the rest of the row. The book and author page URLs are
kept because a row nobody can trace back cannot be checked.

## Filtering

Filter by which contact channels a record actually carries — email, phone,
WhatsApp, **website**, LinkedIn, Instagram, **Facebook**, **X** — either "any of"
or "match all". That is the question that decides whether a page is worth saving:
a lead you cannot reach is not a lead. The author's website filter matters most,
since a site is what the later hops turn into an address.

**Author page and author website are different columns.** The page is their
profile on the site you are browsing — a catalogue's author listing, a Goodreads
profile. The website is their own domain, and it is the one the later hops turn
into an address. schema.org markup routinely publishes `author.url` pointing back
at the same site, which is why the two used to hold the same value; a URL is now
classified by where it points before it is filed.

**A social link is only recorded as the author's when it was found in an author
context** — their page, or the about-the-author block on a book page. A site's
footer profiles belong to the platform, and filing Goodreads' own LinkedIn under
every author would make the column look full while being worse than empty for
outreach. Those are kept separately instead. Author email follows the same rule:
a page-level address is the publisher's general inbox, not the author's.

## Priority and pitch

Each record is scored 0–100 from four signals: the platform's purchase signal,
recency, how many contact channels exist, and how few reviews the book has.

The purchase signal is the strongest qualifier. An author on a vanity imprint has
already paid to publish; one on a paid-review site has already paid to market.
Known domains are matched against the bundled registry of 154 platforms; unknown
ones are classified from the page's own wording, so the tool is useful on a site
it has never seen. `idealPitch` is a starting draft from that signal — edit it,
do not send it as written.

## Export

CSV from the page panel (current page) or from the popup (saved library), with
the columns in `COLUMNS` in `src/content/extract.js`.

## Testing

```bash
npm install
node tests/extension_smoke.mjs
```

Loads the unpacked extension into real Chromium against local fixture pages and
checks extraction, classification, scoring and the panel filters. No network
access needed; the test serves its own fixtures.

## Before publishing to the Chrome Web Store

This extension collects personal data (contact details), which brings it under
the Web Store's Limited Use policy: a privacy policy is required, the disclosure
must match what the code actually does, and data may only be used for the
disclosed purpose. Worth reading before you submit rather than after a rejection.
