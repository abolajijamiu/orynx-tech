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

## Filtering

Filter by which contact channels a record actually carries — email, phone,
WhatsApp, LinkedIn, Instagram — either "any of" or "match all". That is the
question that decides whether a page is worth saving: a lead you cannot reach is
not a lead.

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
