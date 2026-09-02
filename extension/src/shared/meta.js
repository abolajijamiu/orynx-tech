/**
 * Meta tags and microdata.
 *
 * The fallback for pages without JSON-LD. Open Graph book tags and itemprop
 * markup are both common on older publisher sites and cost nothing to read.
 */

import { cleanText, normalizeIsbn, parseCount, toNumber } from "./normalize.js";

function meta(doc, names) {
  for (const name of names) {
    const node =
      doc.querySelector(`meta[property="${name}"]`) ||
      doc.querySelector(`meta[name="${name}"]`) ||
      doc.querySelector(`meta[itemprop="${name}"]`);
    const content = node?.getAttribute("content");
    if (content && content.trim()) return content.trim();
  }
  return null;
}

export function extractMetaBook(doc = document, pageUrl = "") {
  const type = (meta(doc, ["og:type"]) || "").toLowerCase();
  const title = meta(doc, ["og:title", "twitter:title", "citation_title"]);
  const isbn = meta(doc, ["book:isbn", "citation_isbn", "isbn"]);
  const authorTags = [
    ...doc.querySelectorAll('meta[property="book:author"], meta[name="citation_author"]'),
  ]
    .map((n) => n.getAttribute("content"))
    .filter(Boolean);

  // Only claim a book when something actually says so; otherwise every page with
  // an og:title would become a false record.
  const isBook = type.includes("book") || Boolean(isbn) || authorTags.length > 0;
  if (!isBook || !title) return null;

  return {
    title: cleanText(title),
    subtitle: null,
    authors: authorTags.map((name) => ({ name: cleanText(name), url: null, description: null })),
    isbn: normalizeIsbn(isbn),
    publisher: cleanText(meta(doc, ["book:publisher", "citation_publisher", "og:site_name"])),
    publishedDate: meta(doc, ["book:release_date", "citation_publication_date", "article:published_time"]),
    launchDate: meta(doc, ["book:release_date"]),
    description: cleanText(meta(doc, ["og:description", "description", "twitter:description"])),
    language: meta(doc, ["og:locale", "citation_language"]),
    pageCount: toNumber(meta(doc, ["citation_pages", "book:page_count"])),
    coverUrl: meta(doc, ["og:image", "twitter:image", "citation_cover_url"]),
    categories: [],
    averageRating: toNumber(meta(doc, ["book:rating:value", "rating"])),
    reviewsCount: parseCount(meta(doc, ["book:rating:count", "review_count"])),
    ratingsCount: null,
    readersCount: null,
    price: meta(doc, ["product:price:amount", "og:price:amount"]),
    currency: meta(doc, ["product:price:currency", "og:price:currency"]),
    url: meta(doc, ["og:url"]) || pageUrl,
    format: null,
    source: "meta",
  };
}

const ITEMPROP_MAP = {
  name: "title",
  headline: "title",
  isbn: "isbn",
  datePublished: "publishedDate",
  description: "description",
  numberOfPages: "pageCount",
  inLanguage: "language",
  publisher: "publisher",
  ratingValue: "averageRating",
  reviewCount: "reviewsCount",
  ratingCount: "ratingsCount",
};

function itempropValue(node) {
  const tag = node.tagName.toLowerCase();
  if (tag === "meta") return node.getAttribute("content");
  if (tag === "link" || tag === "a") return node.getAttribute("href");
  if (tag === "img") return node.getAttribute("src");
  if (tag === "time") return node.getAttribute("datetime") || node.textContent;
  return node.textContent;
}

/** Read a microdata subtree rooted at an element with itemtype .../Book. */
export function extractMicrodataBooks(doc = document, pageUrl = "") {
  const roots = doc.querySelectorAll('[itemtype*="schema.org/Book" i], [itemtype*="schema.org/Product" i]');
  const books = [];
  for (const root of roots) {
    const record = {
      title: null, subtitle: null, authors: [], isbn: null, publisher: null,
      publishedDate: null, launchDate: null, description: null, language: null,
      pageCount: null, coverUrl: null, categories: [], averageRating: null,
      reviewsCount: null, ratingsCount: null, readersCount: null, price: null,
      currency: null, url: pageUrl, format: null, source: "microdata",
    };
    for (const node of root.querySelectorAll("[itemprop]")) {
      const prop = node.getAttribute("itemprop");
      const raw = itempropValue(node);
      if (!raw) continue;
      if (prop === "author") {
        const name = cleanText(raw);
        if (name) record.authors.push({ name, url: null, description: null });
        continue;
      }
      if (prop === "image" && !record.coverUrl) {
        record.coverUrl = raw;
        continue;
      }
      const field = ITEMPROP_MAP[prop];
      if (!field) continue;
      if (field === "isbn") record.isbn = normalizeIsbn(raw);
      else if (field === "pageCount") record.pageCount = toNumber(raw);
      else if (field === "averageRating") record.averageRating = toNumber(raw);
      else if (field === "reviewsCount") record.reviewsCount = parseCount(raw);
      else if (field === "ratingsCount") record.ratingsCount = parseCount(raw);
      else if (!record[field]) record[field] = cleanText(raw);
    }
    if (record.title && (record.isbn || record.authors.length)) books.push(record);
  }
  return books;
}
