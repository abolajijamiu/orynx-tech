/**
 * Page orchestration: run every extractor, merge, and emit lead records.
 *
 * Extractors are tried in descending order of reliability and merged rather than
 * raced, so a page with partial JSON-LD still gains a review count found only in
 * the DOM. Every field records which extractor produced it.
 */

import { explainRejections, extractHeuristicBooks, pageLooksBooky } from "../shared/heuristics.js";
import { extractJsonLdBooks } from "../shared/jsonld.js";
import { extractMetaBook, extractMicrodataBooks } from "../shared/meta.js";
import { classifyPage, loadRegistry, pitchFor, scoreLead } from "../shared/classify.js";
import { extractContacts, siteDomain } from "../shared/contacts.js";
import { extractDetail } from "../shared/detail.js";
import { extractAuthorProfile, looksLikeAuthorPage } from "../shared/author.js";
import { cleanText, isbn10To13, normalizeTitle, normalizePerson, parseDate, readableText } from "../shared/normalize.js";

/**
 * The export, as a header and the field behind it.
 *
 * Deliberately narrow: these are the columns asked for, in the order asked for,
 * rather than every field the extractor happens to hold. `sourceUrl` and
 * `authorPageUrl` are kept because a row nobody can trace back cannot be
 * checked or defended, and `found_email` and `outreach_message` are left empty
 * for a later AI pass to fill from the rest of the row.
 */
export const EXPORT_COLUMNS = [
  ["Book title", "bookName"],
  ["Description", "description"],
  ["Rating", "averageRating"],
  ["Reviews", "reviewsCount"],
  ["Voters", "votersCount"],
  ["Views", "viewsCount"],
  ["Date published", "publishDate"],
  ["Expected publication", "expectedPublication"],
  ["Pages", "pageCount"],
  ["Genres", "genres"],
  ["Original title", "originalTitle"],
  ["Edition", "edition"],
  ["Language", "language"],
  ["More editions", "moreEditionsUrl"],
  ["Book statistics", "bookStatistics"],
  ["Author name", "author"],
  ["About the author", "authorBio"],
  ["Author email", "email"],
  ["Author phone", "authorPhone"],
  ["Author website", "authorWebsite"],
  ["Facebook", "facebook"],
  ["Instagram", "instagram"],
  ["TikTok", "tiktok"],
  ["Twitter/X", "twitter"],
  ["LinkedIn", "linkedin"],
  ["Other socials", "otherSocials"],
  ["Community reviews", "topReviews"],
  ["Book page", "sourceUrl"],
  ["Author page", "authorPageUrl"],
  ["found_email", "found_email"],
  ["outreach_message", "outreach_message"],
];

export const COLUMNS = EXPORT_COLUMNS.map(([, field]) => field);
export const HEADERS = EXPORT_COLUMNS.map(([header]) => header);

/** One CSV row, in export order. */
export function toRow(record) {
  return COLUMNS.map((field) => {
    const value = record[field];
    return value === null || value === undefined ? "" : String(value);
  });
}

export function toCsv(records) {
  const escape = (value) => (/[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value);
  const lines = [HEADERS.map(escape).join(",")];
  for (const record of records) lines.push(toRow(record).map(escape).join(","));
  // A BOM so Excel opens UTF-8 correctly.
  return "\ufeff" + lines.join("\n");
}

function mergeBooks(candidates) {
  // Later extractors only fill gaps; the earlier, more reliable one wins.
  const byKey = new Map();
  for (const book of candidates) {
    if (!book || !book.title) continue;
    const key = book.isbn || `${normalizeTitle(book.title)}|${normalizePerson(book.authors?.[0]?.name || "")}`;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, { ...book });
      continue;
    }
    for (const [field, value] of Object.entries(book)) {
      if (value === null || value === undefined || value === "") continue;
      if (Array.isArray(value)) {
        if (!existing[field] || existing[field].length === 0) existing[field] = value;
      } else if (existing[field] === null || existing[field] === undefined || existing[field] === "") {
        existing[field] = value;
      }
    }
  }
  return [...byKey.values()];
}

function siteName(doc, classification) {
  if (classification.company) return classification.company;
  const meta = doc.querySelector('meta[property="og:site_name"]')?.getAttribute("content");
  if (meta) return cleanText(meta);
  const title = doc.title || "";
  const parts = title.split(/[|–—\-]/);
  return cleanText(parts.length > 1 ? parts[parts.length - 1] : title) || null;
}

/** Run everything and return records shaped for the spreadsheet. */
export function extractPage(doc = document, pageUrl = location.href, registryJson = null) {
  if (registryJson) loadRegistry(registryJson);

  const { books: jsonLdBooks, site } = extractJsonLdBooks(doc, pageUrl);
  const microdataBooks = extractMicrodataBooks(doc, pageUrl);
  const metaBook = extractMetaBook(doc, pageUrl);

  const pageText = readableText(doc.body);
  // Classify by the domain the site claims as its own, so a page served from a
  // CDN, a staging host or a local mirror still resolves to the right platform.
  const classification = classifyPage(siteDomain(doc, pageUrl) || new URL(pageUrl).hostname, pageText);
  // A known book platform, a page that talks like a publisher, or one simply
  // shaped like a book catalogue all lower the evidence a listing card must
  // supply: a cover and an author name become enough. This is what makes a site
  // nobody has configured work on first contact.
  const bookContext =
    classification.known ||
    classification.category !== "unknown" ||
    pageLooksBooky(doc, pageText);

  let candidates = [...jsonLdBooks, ...microdataBooks];
  if (metaBook) candidates.push(metaBook);
  // Only fall back to guessing at structure when nothing declared any.
  if (candidates.length === 0) {
    candidates = extractHeuristicBooks(doc, pageUrl, { bookContext });
  }

  const merged = mergeBooks(candidates);
  const contacts = extractContacts(doc, pageUrl);
  // Deep extraction only makes sense where the page is about a single book;
  // on a listing, labelled fields and review blocks belong to no one record.
  const isDetailPage = merged.length === 1;
  const detail = isDetailPage ? extractDetail(doc, pageUrl) : null;
  const company = siteName(doc, classification);
  const capturedAt = new Date().toISOString();

  const primaryEmail = contacts.emails[0]?.value || null;
  const contactPage = contacts.contactPages[0]?.url || null;
  const country = classification.country || site?.country || "";

  return merged.map((book) => {
    const published = parseDate(book.publishedDate);
    const launch = parseDate(book.launchDate);
    let isbn = book.isbn;
    if (isbn && isbn.length === 10) isbn = isbn10To13(isbn) || isbn;

    const stats = detail?.statistics || {};
    const authorSocials = detail?.authorSocials || {};
    const record = {
      bookName: book.title,
      originalTitle: detail?.originalTitle || null,
      author: (book.authors || []).map((a) => a.name).filter(Boolean).join("; "),
      authorUrl: (book.authors || []).map((a) => a.url).filter(Boolean)[0] || null,
      series: detail?.series || null,
      edition: detail?.edition || null,
      awards: detail?.awards || null,
      website: pageUrl,
      company,
      category: classification.category,
      country,
      contactPage,
      email: (detail?.authorEmails || [])[0] || primaryEmail,
      allEmails: contacts.emails.map((e) => e.value),
      // An author's own profile outranks the site's footer links.
      linkedin: authorSocials.linkedin || contacts.socials.linkedin || null,
      instagram: authorSocials.instagram || contacts.socials.instagram || null,
      twitter: authorSocials.twitter || contacts.socials.twitter || null,
      facebook: authorSocials.facebook || contacts.socials.facebook || null,
      tiktok: authorSocials.tiktok || null,
      youtube: authorSocials.youtube || contacts.socials.youtube || null,
      substack: authorSocials.substack || null,
      authorWebsite: detail?.authorWebsite || (book.authors || [])[0]?.url || null,
      authorBio: detail?.authorBio || null,
      topReviews: (detail?.reviews || [])
        .map((review) => (review.rating ? `[${review.rating}/5] ${review.text}` : review.text))
        .join(" || ") || null,
      phone: contacts.phones[0] || null,
      allPhones: contacts.phones,
      whatsapp: contacts.whatsapp[0] || null,
      services: (classification.services || []).join("; "),
      publishDate:
        published.iso ||
        (published.year ? String(published.year) : null) ||
        detail?.labelledPublishDate ||
        null,
      publishedYear: published.year,
      expectedPublication: detail?.expectedPublication || null,
      firstPublished: detail?.firstPublished || null,
      launchDate: launch.iso || (launch.year ? String(launch.year) : null),
      readersCount: book.readersCount ?? null,
      // Prefer what the book itself declared; fall back to counts read off the page.
      reviewsCount: book.reviewsCount ?? stats.reviewsCount ?? null,
      ratingsCount: book.ratingsCount ?? stats.ratingsCount ?? null,
      votersCount: stats.votersCount ?? null,
      viewsCount: stats.viewsCount ?? null,
      wantToReadCount: stats.wantToReadCount ?? null,
      currentlyReadingCount: stats.currentlyReadingCount ?? null,
      editionsCount: stats.editionsCount ?? null,
      moreEditionsUrl: detail?.moreEditionsUrl || null,
      averageRating: book.averageRating ?? null,
      isbn: isbn || null,
      publisher: book.publisher || null,
      price: book.price ? `${book.price}${book.currency ? " " + book.currency : ""}` : null,
      pageCount: book.pageCount ?? null,
      language: book.language || detail?.labelledLanguage || null,
      format: book.format || null,
      genres: (book.categories || []).join("; ") || detail?.labelledGenres || null,
      coverUrl: book.coverUrl || null,
      description: book.description || null,
      sourceUrl: book.url || pageUrl,
      extractedBy: book.source,
      isDetailPage,
      // Left blank on purpose for a later AI pass to complete.
      found_email: "",
      outreach_message: "",
      platformSignal: classification.signal,
      platformId: classification.platformId,
      platformKnown: classification.known,
      signal: classification.signal,
      capturedAt,
    };

    // "Book statistics" gathers the counts a page publishes about itself into one
    // readable cell, since they are individually sparse and jointly meaningful.
    record.bookStatistics = [
      stats.wantToReadCount ? `${stats.wantToReadCount} want to read` : null,
      stats.currentlyReadingCount ? `${stats.currentlyReadingCount} currently reading` : null,
      stats.editionsCount ? `${stats.editionsCount} editions` : null,
      record.ratingsCount ? `${record.ratingsCount} ratings` : null,
    ].filter(Boolean).join(" · ") || null;
    // Voters are the people who rated it, as distinct from those who wrote a review.
    record.votersCount = stats.votersCount ?? record.ratingsCount ?? null;
    record.otherSocials = [
      authorSocials.youtube || null,
      authorSocials.substack || null,
      authorSocials.threads || null,
      authorSocials.bluesky || null,
      authorSocials.patreon || null,
      authorSocials.goodreads || null,
    ].filter(Boolean).join(" ; ") || null;
    record.authorPhone = record.authorPhone || null;

    const scored = scoreLead(record, classification);
    record.priority = scored.score;
    record.tier = scored.tier;
    record.priorityReasons = scored.reasons;
    record.idealPitch = pitchFor(classification.signal, classification.services);
    return record;
  });
}

/** Filter predicate used by both the in-page panel and the export. */
export function matchesFilter(record, filter = {}) {
  const { channels = [], requireAll = false, minPriority = 0, tiers = [], query = "" } = filter;

  if (record.priority < minPriority) return false;
  if (tiers.length && !tiers.includes(record.tier)) return false;

  if (channels.length) {
    const present = channels.filter((channel) => Boolean(record[channel]));
    if (requireAll ? present.length !== channels.length : present.length === 0) return false;
  }

  if (query) {
    const needle = query.toLowerCase();
    const haystack = [record.bookName, record.author, record.company, record.publisher]
      .filter(Boolean).join(" ").toLowerCase();
    if (!haystack.includes(needle)) return false;
  }
  return true;
}


/**
 * Why a page produced what it did.
 *
 * When a page that obviously contains books yields none, the useful question is
 * which stage gave up and why. This reports that in a form a user can paste
 * back, which is far faster than guessing at someone else's markup.
 */
export function diagnosePage(doc = document, pageUrl = location.href, registryJson = null) {
  if (registryJson) loadRegistry(registryJson);

  const pageText = readableText(doc.body);
  const classification = classifyPage(siteDomain(doc, pageUrl) || new URL(pageUrl).hostname, pageText);
  const bookContext =
    classification.known ||
    classification.category !== "unknown" ||
    pageLooksBooky(doc, pageText);
  const { books: jsonLdBooks } = extractJsonLdBooks(doc, pageUrl);
  const contacts = extractContacts(doc, pageUrl);
  const records = extractPage(doc, pageUrl, null);

  return {
    url: pageUrl,
    title: doc.title,
    found: records.length,
    platform: {
      id: classification.platformId,
      known: classification.known,
      category: classification.category,
      signal: classification.signal,
      bookContext,
    },
    structured: {
      jsonLdScripts: doc.querySelectorAll('script[type="application/ld+json"]').length,
      jsonLdBooks: jsonLdBooks.length,
      microdataRoots: doc.querySelectorAll('[itemtype*="schema.org/Book" i], [itemtype*="schema.org/Product" i]').length,
      ogType: doc.querySelector('meta[property="og:type"]')?.getAttribute("content") || null,
      bookMetaTags: doc.querySelectorAll('meta[property^="book:"], meta[name^="citation_"]').length,
    },
    contacts: {
      emails: contacts.emails.length,
      phones: contacts.phones.length,
      whatsapp: contacts.whatsapp.length,
      socials: Object.keys(contacts.socials),
      contactPages: contacts.contactPages.length,
    },
    rejectedSamples: records.length ? [] : explainRejections(doc, pageUrl, bookContext),
  };
}


/**
 * Every book link on a listing page, for the queue that visits them.
 *
 * Deduplicated and same-origin only: a catalogue links out to retailers and
 * social sites, and following those is neither useful nor polite.
 */
export function collectBookLinks(doc = document, pageUrl = location.href) {
  const records = extractPage(doc, pageUrl, null);
  const origin = new URL(pageUrl).origin;
  const seen = new Set();
  const links = [];

  for (const record of records) {
    const href = record.sourceUrl;
    if (!href || href === pageUrl) continue;
    let url;
    try {
      url = new URL(href, pageUrl);
    } catch {
      continue;
    }
    if (url.origin !== origin) continue;
    const clean = url.origin + url.pathname + url.search;
    if (seen.has(clean)) continue;
    seen.add(clean);
    links.push({ url: clean, title: record.bookName, author: record.author || null });
  }
  return links;
}


/** The author profile for this page, when the page is about an author. */
export function extractAuthor(doc = document, pageUrl = location.href) {
  if (!looksLikeAuthorPage(doc, pageUrl)) return null;
  const profile = extractAuthorProfile(doc, pageUrl);
  // A profile with neither a name nor anything to reach them by is not worth saving.
  if (!profile.authorName) return null;
  return profile;
}

/**
 * Distinct author pages worth visiting, from records already collected.
 *
 * One visit per author, not per book: an author with six titles has one page,
 * and fetching it six times would be both slower and ruder.
 */
export function collectAuthorLinks(records, { sameOriginAs = null } = {}) {
  const seen = new Set();
  const links = [];
  for (const record of records || []) {
    const href = record.authorUrl;
    if (!href) continue;
    let url;
    try {
      url = new URL(href);
    } catch {
      continue;
    }
    if (!/^https?:$/.test(url.protocol)) continue;
    if (sameOriginAs && url.origin !== sameOriginAs) continue;
    const clean = url.origin + url.pathname;
    if (seen.has(clean)) continue;
    seen.add(clean);
    links.push({ url: clean, title: record.author || clean, author: record.author || null });
  }
  return links;
}
