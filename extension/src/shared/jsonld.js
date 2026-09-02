/**
 * schema.org extraction.
 *
 * The highest-value path by far: publishers, retailers and review sites publish
 * Book, Product and Review markup because search engines reward it. A page that
 * has it needs no site-specific rules at all, which is what lets this work on a
 * site nobody has seen before.
 */

import { cleanText, normalizeIsbn, parseCount, toNumber } from "./normalize.js";

const BOOK_TYPES = new Set(["book", "audiobook", "ebook"]);
const PRODUCT_TYPES = new Set(["product", "individualproduct", "productmodel"]);
const REVIEW_TYPES = new Set(["review", "bookreview"]);
const LIST_TYPES = new Set(["itemlist", "collectionpage"]);

function flatten(data, out = []) {
  if (Array.isArray(data)) {
    data.forEach((item) => flatten(item, out));
  } else if (data && typeof data === "object") {
    if (data["@graph"]) flatten(data["@graph"], out);
    out.push(data);
    // Lists wrap the real records one level down.
    for (const key of ["itemListElement", "mainEntity", "hasPart"]) {
      if (data[key]) flatten(data[key], out);
    }
    if (data.item) flatten(data.item, out);
  }
  return out;
}

export function parseJsonLd(doc = document) {
  const objects = [];
  for (const node of doc.querySelectorAll('script[type="application/ld+json"]')) {
    const raw = node.textContent;
    if (!raw || !raw.trim()) continue;
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      // Trailing commas are common in hand-written markup; one salvage attempt.
      try {
        data = JSON.parse(raw.replace(/,\s*([}\]])/g, "$1"));
      } catch {
        continue;
      }
    }
    flatten(data, objects);
  }
  return objects;
}

export function typesOf(obj) {
  const raw = obj["@type"] ?? obj.type ?? [];
  const values = Array.isArray(raw) ? raw : [raw];
  return new Set(values.map((v) => String(v).toLowerCase()));
}

function asList(value) {
  if (value === null || value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

function nameOf(value) {
  if (typeof value === "string") return cleanText(value);
  if (value && typeof value === "object") return cleanText(value.name || value["@id"]);
  return null;
}

/** True when this object describes a book we should capture. */
export function looksLikeBook(obj) {
  const types = typesOf(obj);
  for (const type of types) if (BOOK_TYPES.has(type)) return true;
  // A Product with an ISBN or an author is a book in a shop's markup.
  for (const type of types) {
    if (PRODUCT_TYPES.has(type) && (obj.isbn || obj.gtin13 || obj.author)) return true;
  }
  // A Review whose subject is a book carries the book inside it.
  for (const type of types) {
    if (REVIEW_TYPES.has(type) && obj.itemReviewed) return true;
  }
  return false;
}

export function bookFromJsonLd(obj, pageUrl) {
  // A review wraps its subject; unwrap and keep the review's own counts.
  let source = obj;
  let reviewWrapper = null;
  const types = typesOf(obj);
  for (const type of types) {
    if (REVIEW_TYPES.has(type) && obj.itemReviewed) {
      reviewWrapper = obj;
      source = obj.itemReviewed;
      break;
    }
  }

  const authors = [];
  for (const entry of [...asList(source.author), ...asList(source.creator)]) {
    const name = nameOf(entry);
    if (!name) continue;
    authors.push({
      name,
      url: entry && typeof entry === "object" ? entry.url || entry.sameAs || null : null,
      description: entry && typeof entry === "object" ? cleanText(entry.description) : null,
    });
  }

  const rating = source.aggregateRating || reviewWrapper?.aggregateRating || {};
  let image = source.image;
  if (Array.isArray(image)) image = image[0];
  if (image && typeof image === "object") image = image.url;

  const offers = asList(source.offers)[0];
  const workExample = asList(source.workExample)[0];
  const isbn = source.isbn || source.gtin13 || (workExample && workExample.isbn) || null;

  return {
    title: cleanText(source.name || source.headline),
    subtitle: cleanText(source.alternativeHeadline),
    authors,
    isbn: normalizeIsbn(typeof isbn === "string" ? isbn : null),
    publisher: nameOf(source.publisher),
    publishedDate: source.datePublished || source.copyrightYear || null,
    // Some sites use these for a launch or pre-order date distinct from publication.
    launchDate: source.releaseDate || source.datePosted || null,
    description: cleanText(source.description || source.abstract),
    language: typeof source.inLanguage === "string" ? source.inLanguage : null,
    pageCount: toNumber(source.numberOfPages),
    coverUrl: typeof image === "string" ? image : null,
    categories: asList(source.genre).filter((g) => typeof g === "string"),
    averageRating: toNumber(rating.ratingValue),
    reviewsCount: parseCount(rating.reviewCount ?? source.reviewCount),
    ratingsCount: parseCount(rating.ratingCount),
    readersCount: parseCount(source.readerCount ?? source.interactionCount),
    price: offers && typeof offers === "object" && offers.price !== undefined ? String(offers.price) : null,
    currency: offers && typeof offers === "object" ? offers.priceCurrency || null : null,
    url: typeof source.url === "string" ? source.url : pageUrl,
    format: source.bookFormat ? String(source.bookFormat).split("/").pop() : null,
    source: "jsonld",
  };
}

/** Site identity, which schema.org exposes far more reliably than a <title>. */
export function siteInfoFromJsonLd(objects) {
  for (const obj of objects) {
    const types = typesOf(obj);
    if (types.has("organization") || types.has("website") || types.has("publisher")) {
      const address = obj.address || {};
      return {
        name: cleanText(obj.name),
        url: typeof obj.url === "string" ? obj.url : null,
        country: cleanText(address.addressCountry?.name || address.addressCountry) || null,
        email: cleanText(obj.email),
        telephone: cleanText(obj.telephone),
        sameAs: asList(obj.sameAs).filter((s) => typeof s === "string"),
      };
    }
  }
  return null;
}

export function extractJsonLdBooks(doc = document, pageUrl = "") {
  const objects = parseJsonLd(doc);
  const books = [];
  for (const obj of objects) {
    if (!looksLikeBook(obj)) continue;
    const book = bookFromJsonLd(obj, pageUrl);
    if (book.title) books.push(book);
  }
  return { books, site: siteInfoFromJsonLd(objects) };
}
