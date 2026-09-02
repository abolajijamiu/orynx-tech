/**
 * Page orchestration: run every extractor, merge, and emit lead records.
 *
 * Extractors are tried in descending order of reliability and merged rather than
 * raced, so a page with partial JSON-LD still gains a review count found only in
 * the DOM. Every field records which extractor produced it.
 */

import { extractHeuristicBooks } from "../shared/heuristics.js";
import { extractJsonLdBooks } from "../shared/jsonld.js";
import { extractMetaBook, extractMicrodataBooks } from "../shared/meta.js";
import { classifyPage, loadRegistry, pitchFor, scoreLead } from "../shared/classify.js";
import { extractContacts } from "../shared/contacts.js";
import { cleanText, isbn10To13, normalizeTitle, normalizePerson, parseDate, readableText } from "../shared/normalize.js";

export const COLUMNS = [
  "bookName", "author", "website", "company", "category", "country",
  "contactPage", "email", "linkedin", "instagram", "phone", "whatsapp",
  "services", "idealPitch", "priority", "tier",
  "launchDate", "publishDate", "readersCount", "reviewsCount", "ratingsCount",
  "averageRating", "isbn", "publisher", "price", "pageCount", "language",
  "format", "coverUrl", "sourceUrl", "extractedBy", "capturedAt",
];

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

  let candidates = [...jsonLdBooks, ...microdataBooks];
  if (metaBook) candidates.push(metaBook);
  // Only fall back to guessing at structure when nothing declared any.
  if (candidates.length === 0) {
    candidates = extractHeuristicBooks(doc, pageUrl);
  }

  const merged = mergeBooks(candidates);
  const contacts = extractContacts(doc, pageUrl);
  const pageText = readableText(doc.body);
  const classification = classifyPage(new URL(pageUrl).hostname, pageText);
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

    const record = {
      bookName: book.title,
      author: (book.authors || []).map((a) => a.name).filter(Boolean).join("; "),
      authorUrls: (book.authors || []).map((a) => a.url).filter(Boolean),
      website: pageUrl,
      company,
      category: classification.category,
      country,
      contactPage,
      email: primaryEmail,
      allEmails: contacts.emails.map((e) => e.value),
      linkedin: contacts.socials.linkedin || null,
      instagram: contacts.socials.instagram || null,
      twitter: contacts.socials.twitter || null,
      facebook: contacts.socials.facebook || null,
      phone: contacts.phones[0] || null,
      allPhones: contacts.phones,
      whatsapp: contacts.whatsapp[0] || null,
      services: (classification.services || []).join("; "),
      publishDate: published.iso || (published.year ? String(published.year) : null),
      publishedYear: published.year,
      launchDate: launch.iso || (launch.year ? String(launch.year) : null),
      readersCount: book.readersCount ?? null,
      reviewsCount: book.reviewsCount ?? null,
      ratingsCount: book.ratingsCount ?? null,
      averageRating: book.averageRating ?? null,
      isbn: isbn || null,
      publisher: book.publisher || null,
      price: book.price ? `${book.price}${book.currency ? " " + book.currency : ""}` : null,
      pageCount: book.pageCount ?? null,
      language: book.language || null,
      format: book.format || null,
      coverUrl: book.coverUrl || null,
      description: book.description || null,
      sourceUrl: book.url || pageUrl,
      extractedBy: book.source,
      platformId: classification.platformId,
      platformKnown: classification.known,
      signal: classification.signal,
      capturedAt,
    };

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
