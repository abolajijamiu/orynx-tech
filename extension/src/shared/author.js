/**
 * Author-page extraction.
 *
 * A book page names its author; the author's own page is where the biography,
 * the links and — the point of the exercise — any published address actually
 * live. This reads such a page generically, so it works on a publisher's author
 * listing as readily as on a catalogue's profile.
 */

import { cleanText, normalizePerson, parseCount, readableText } from "./normalize.js";
import { authorLinks } from "./detail.js";
import { asAuthorWebsite } from "./links.js";

const AUTHOR_URL_RE = /\/(?:author|authors|writer|writers|contributor|contributors|profile)\//i;

const NAME_SELECTORS = [
  "[itemprop='name']", "[class*='authorName' i]", "[class*='author-name' i]",
  "[class*='profileName' i]", "h1",
];

const BIO_SELECTORS = [
  "[class*='authorBio' i]", "[class*='author-bio' i]", "[class*='biography' i]",
  "[itemprop='description']", "[class*='bio' i]", "[class*='about' i]",
];

// Labelled details author pages commonly publish.
const DETAIL_LABELS = {
  born: "born",
  birthplace: "born",
  "born in": "born",
  died: "died",
  website: "website",
  "web site": "website",
  homepage: "website",
  genre: "genres",
  genres: "genres",
  influences: "influences",
  member: "memberSince",
  "member since": "memberSince",
  location: "location",
  lives: "location",
  nationality: "nationality",
  twitter: "twitterHandle",
};

/** Does this page look like it is about an author rather than a book? */
export function looksLikeAuthorPage(doc = document, pageUrl = "") {
  if (AUTHOR_URL_RE.test(pageUrl)) return true;
  const text = readableText(doc.body).slice(0, 4000);
  if (/\b(about the author|books by|author of|other books by)\b/i.test(text)) return true;
  return Boolean(doc.querySelector("[class*='authorBio' i], [class*='author-bio' i]"));
}

function firstText(doc, selectors, { maxLength = 200, minLength = 2 } = {}) {
  for (const selector of selectors) {
    for (const node of doc.querySelectorAll(selector)) {
      const text = readableText(node);
      if (text && text.length >= minLength && text.length <= maxLength) return text;
    }
  }
  return null;
}

function authorName(doc) {
  const meta = doc.querySelector('meta[property="og:title"]')?.getAttribute("content");
  const fromMeta = cleanText(meta);
  const candidate = firstText(doc, NAME_SELECTORS, { maxLength: 80 }) || fromMeta;
  if (!candidate) return null;
  // Titles like "Amara Nwosu (Author of Hollow Bones)" carry the name first.
  return cleanText(candidate.split(/\s*[(|·–—]\s*/)[0]);
}

function authorBio(doc) {
  let best = null;
  for (const selector of BIO_SELECTORS) {
    for (const node of doc.querySelectorAll(selector)) {
      const text = readableText(node);
      if (!text || text.length < 60) continue;
      if (!best || text.length > best.length) best = text;
      if (best.length > 2000) break;
    }
    if (best) break;
  }
  if (best) return best.slice(0, 6000);

  // Nothing marked up: take the longest paragraph on the page.
  let longest = "";
  for (const node of doc.querySelectorAll("p")) {
    const text = readableText(node);
    if (text.length > longest.length) longest = text;
  }
  return longest.length >= 120 ? longest.slice(0, 6000) : null;
}

/**
 * The value beside a label.
 *
 * "Website: amaranwosu.example" is usually a link whose text is a shortened
 * display form, so the href is the usable value and the text is not.
 */
function labelledValue(node) {
  const link = node.querySelector ? node.querySelector("a[href]") : null;
  const href = link?.getAttribute("href");
  if (href && /^https?:\/\//i.test(href)) return href;
  return readableText(node);
}

function labelledDetails(doc) {
  const found = {};
  const record = (label, value) => {
    const key = DETAIL_LABELS[String(label || "").toLowerCase().replace(/[:\s]+$/, "").trim()];
    const text = cleanText(value);
    if (key && text && text.length < 300 && !found[key]) found[key] = text;
  };
  for (const dt of doc.querySelectorAll("dt")) {
    const dd = dt.nextElementSibling;
    if (dd?.tagName === "DD") record(readableText(dt), labelledValue(dd));
  }
  for (const node of doc.querySelectorAll("span, div, strong, b, label")) {
    const label = readableText(node);
    if (!label || label.length > 30) continue;
    const sibling = node.nextElementSibling;
    if (sibling) record(label, labelledValue(sibling));
  }
  return found;
}

/** How many books the page credits to this author. */
/** Accept "https://x" or a bare "x.com"; reject anything that is not a host. */
function normalizeSite(value) {
  if (!value) return null;
  const text = String(value).trim();
  if (/^https?:\/\//i.test(text)) return text;
  if (/^[a-z0-9-]+(\.[a-z0-9-]+)+(\/|$)/i.test(text)) return `https://${text}`;
  return null;
}

function bookCount(doc) {
  const text = readableText(doc.body);
  const stated = text.match(/([\d,]+)\s*(?:books?|titles?|works?)\b/i);
  if (stated) return parseCount(stated[1]);
  const links = new Set();
  for (const link of doc.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href") || "";
    if (/\/(?:book|books|title|titles)\//i.test(href)) links.add(href.split("?")[0]);
  }
  return links.size || null;
}

/**
 * Build an author profile from the page.
 *
 * `sourceUrl` is recorded on the profile so every field it contributes can be
 * traced back to the page it came from.
 */
export function extractAuthorProfile(doc = document, pageUrl = location.href) {
  const links = authorLinks(doc, doc.body);
  const details = labelledDetails(doc);
  const name = authorName(doc);

  return {
    authorName: name,
    authorNameKey: name ? normalizePerson(name) : null,
    authorPageUrl: pageUrl,
    authorBio: authorBio(doc),
    // Measured against the page it was found on, so a link back into the same
    // site is recognised as navigation rather than the author's own domain.
    authorWebsite:
      asAuthorWebsite(normalizeSite(details.website), pageUrl) ||
      asAuthorWebsite(links.website, pageUrl) ||
      null,
    authorEmail: links.emails[0] || null,
    authorEmails: links.emails,
    authorSocials: links.socials,
    authorBorn: details.born || null,
    authorLocation: details.location || details.nationality || null,
    authorGenres: details.genres || null,
    authorInfluences: details.influences || null,
    authorBookCount: bookCount(doc),
    capturedAt: new Date().toISOString(),
  };
}

/**
 * Merge a profile onto a saved book record.
 *
 * Only fills gaps, so a detail page's better answer is never overwritten, and
 * only claims the author's own site when the record has none.
 */
export function applyAuthorProfile(record, profile) {
  const updated = { ...record };
  const set = (field, value) => {
    if (value && (updated[field] === null || updated[field] === undefined || updated[field] === "")) {
      updated[field] = value;
    }
  };

  set("authorBio", profile.authorBio);
  set("authorWebsite", profile.authorWebsite);
  set("email", profile.authorEmail);
  set("authorPageUrl", profile.authorPageUrl);
  set("authorBorn", profile.authorBorn);
  set("authorLocation", profile.authorLocation);
  set("authorGenres", profile.authorGenres);
  set("authorBookCount", profile.authorBookCount);

  for (const [network, url] of Object.entries(profile.authorSocials || {})) {
    set(network, url);
  }
  return updated;
}
