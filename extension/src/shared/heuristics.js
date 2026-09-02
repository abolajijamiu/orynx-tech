/**
 * DOM heuristics: the last resort, for pages with no structured markup at all.
 *
 * Deliberately conservative. A wrong book record is worse than a missing one,
 * so a candidate is kept only when a title is accompanied by corroborating
 * evidence — a byline, an ISBN, or a price — rather than on layout alone.
 */

import { cleanText, normalizeIsbn, parseCount, readableText, splitAuthors } from "./normalize.js";

const ISBN_RE = /\b(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx])\b/;
// Byline parsing. Two rules do the work:
//  - a name token ends in a lowercase letter, so "Blake" is a name and "ISBN"
//    is an acronym that terminates the match;
//  - field labels that commonly follow a byline are excluded outright, since
//    "Published" and "Price" are otherwise perfectly good-looking names.
const STOP_WORDS = [
  "Published", "Publication", "Publisher", "Price", "Reviews", "Review", "Rating",
  "Ratings", "Pages", "Page", "Format", "Available", "Release", "Released",
  "Buy", "Add", "Read", "More", "Details", "Paperback", "Hardcover", "Hardback",
  "Ebook", "Kindle", "Audiobook", "Genre", "Category", "Series", "Edition",
  "Language", "Imprint", "Sale", "Preorder", "Order", "View", "Learn", "Shop",
];
// Lowercase particles carried inside many names: Ngugi wa Thiong'o, Ludwig van
// Beethoven, Ahmed ibn Fadlan. Without these the surname is silently truncated.
const PARTICLES = [
  "van", "von", "de", "del", "della", "der", "den", "da", "di", "du", "dos",
  "la", "le", "bin", "ibn", "al", "wa", "ter", "ten", "af", "av",
];

const NAME_TOKEN =
  `(?!(?:${STOP_WORDS.join("|")})\\b)(?:[A-Z]\\.|[A-Z][\\p{L}'\u2019\\-]*[\\p{Ll}])`;
const NAME_PART = `(?:${NAME_TOKEN}|(?:${PARTICLES.join("|")})\\b)`;
const NAME = `${NAME_TOKEN}(?:\\s+${NAME_PART}){0,4}`;
const BYLINE_RE = new RegExp(
  `\\b(?:by|written by|author)\\s*:?\\s+(${NAME}(?:\\s*(?:,|and|&)\\s*${NAME})*)`,
  "u",
);
const TRAILING_PARTICLE = new RegExp(`\\s+(?:${PARTICLES.join("|")})$`, "i");

const REVIEWS_RE = /([\d,]+)\s*(?:customer\s*)?(?:reviews?|ratings?)\b/i;
const READERS_RE = /([\d,]+)\s*(?:readers?|reads|people are reading|currently reading)\b/i;
const PUBDATE_RE = /(?:published|publication date|release date|pub date|first published|on sale)\s*:?\s*([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]+ \d{4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{4}|\d{4})/i;
const LAUNCH_RE = /(?:launch(?:es|ing|ed)?|releases?|available)\s*(?:on|date)?\s*:?\s*([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]+ \d{4}|\d{4}-\d{2}-\d{2})/i;

// Containers that usually hold one book each on a listing page.
const CARD_SELECTORS = [
  "article", "li.product", "div.product", ".book", ".book-card", ".book-item",
  ".product-item", ".entry", ".post", ".card", "[class*='book']", "[class*='product']",
];

function textOf(node) {
  return readableText(node);
}

function findTitle(node) {
  for (const selector of ["h1", "h2", "h3", ".title", "[class*='title']", "a"]) {
    const found = node.querySelector(selector);
    const text = textOf(found);
    if (text && text.length >= 2 && text.length <= 200) return text;
  }
  return null;
}

/** Pull a book out of one container, or null when the evidence is too thin. */
export function bookFromNode(node, pageUrl) {
  const text = textOf(node);
  if (!text || text.length > 4000) return null;

  const title = findTitle(node);
  if (!title) return null;

  const isbnMatch = text.match(ISBN_RE);
  const bylineMatch = text.match(BYLINE_RE);
  const priceMatch = text.match(/[$£€]\s?\d+(?:[.,]\d{2})?/);

  // Corroboration rule: a heading alone is not a book.
  if (!isbnMatch && !bylineMatch && !priceMatch) return null;

  const link = node.querySelector("a[href]");
  const img = node.querySelector("img[src], img[data-src]");
  const pubMatch = text.match(PUBDATE_RE);
  const launchMatch = text.match(LAUNCH_RE);
  const reviewsMatch = text.match(REVIEWS_RE);
  const readersMatch = text.match(READERS_RE);
  const ratingMatch = text.match(/([\d.]+)\s*(?:out of|\/)\s*5/i);

  return {
    title,
    subtitle: null,
    authors: bylineMatch
      ? splitAuthors(bylineMatch[1].replace(TRAILING_PARTICLE, ""))
          .map((name) => ({ name, url: null, description: null }))
      : [],
    isbn: isbnMatch ? normalizeIsbn(isbnMatch[1]) : null,
    publisher: null,
    publishedDate: pubMatch ? pubMatch[1] : null,
    launchDate: launchMatch ? launchMatch[1] : null,
    description: null,
    language: null,
    pageCount: null,
    coverUrl: img ? img.getAttribute("src") || img.getAttribute("data-src") : null,
    categories: [],
    averageRating: ratingMatch ? Number(ratingMatch[1]) : null,
    reviewsCount: reviewsMatch ? parseCount(reviewsMatch[1]) : null,
    ratingsCount: null,
    readersCount: readersMatch ? parseCount(readersMatch[1]) : null,
    price: priceMatch ? priceMatch[0] : null,
    currency: null,
    url: link ? new URL(link.getAttribute("href"), pageUrl).href : pageUrl,
    format: null,
    source: "heuristic",
  };
}

export function extractHeuristicBooks(doc = document, pageUrl = "", limit = 200) {
  const seen = new Set();
  const books = [];
  const nodes = doc.querySelectorAll(CARD_SELECTORS.join(","));

  for (const node of nodes) {
    // Skip a container that merely wraps other candidates; the inner one wins.
    if (node.querySelector(CARD_SELECTORS.join(","))) continue;
    const book = bookFromNode(node, pageUrl);
    if (!book) continue;
    const key = `${book.title.toLowerCase()}|${book.authors[0]?.name?.toLowerCase() || ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    books.push(book);
    if (books.length >= limit) break;
  }

  // A detail page often has no card at all; read the page itself as one record.
  if (books.length === 0 && doc.body) {
    const single = bookFromNode(doc.body, pageUrl);
    if (single) books.push(single);
  }
  return books;
}
