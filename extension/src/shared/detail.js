/**
 * Deep extraction for a single book's own page.
 *
 * A listing gives a title and a link; the detail page is where the data worth
 * having lives. Everything here is generic — labelled fields, an "about the
 * author" section, review blocks — rather than tied to one site, so it works on
 * a publisher's page as readily as on a catalogue.
 */

import { cleanText, parseCount, readableText, toNumber } from "./normalize.js";
import { isPlatformHost, isSameSite } from "./links.js";

// Labels sites print beside a value, mapped to our field names. Matching is on
// the normalised label, so "Original Title:" and "original title" both hit.
const FIELD_LABELS = {
  "original title": "originalTitle",
  "original language": "originalLanguage",
  language: "language",
  edition: "edition",
  "edition language": "language",
  "this edition": "edition",
  format: "format",
  pages: "pageCount",
  "page count": "pageCount",
  "number of pages": "pageCount",
  isbn: "isbn",
  isbn13: "isbn",
  "isbn-13": "isbn",
  asin: "asin",
  publisher: "publisher",
  published: "publishDate",
  "publication date": "publishDate",
  "publish date": "publishDate",
  "first published": "firstPublished",
  "expected publication": "expectedPublication",
  "release date": "launchDate",
  "publication year": "publishDate",
  series: "series",
  genre: "genresText",
  genres: "genresText",
  categories: "genresText",
  "literary awards": "awards",
  translator: "translator",
  illustrator: "illustrator",
};

const SOCIAL_PATTERNS = {
  facebook: /https?:\/\/(?:www\.)?facebook\.com\/[A-Za-z0-9_.\-]{3,}/i,
  instagram: /https?:\/\/(?:www\.)?instagram\.com\/[A-Za-z0-9_.]{2,}/i,
  twitter: /https?:\/\/(?:www\.)?(?:twitter|x)\.com\/[A-Za-z0-9_]{2,}/i,
  tiktok: /https?:\/\/(?:www\.)?tiktok\.com\/@[A-Za-z0-9_.]{2,}/i,
  linkedin: /https?:\/\/(?:[a-z]{2,3}\.)?linkedin\.com\/(?:in|company)\/[A-Za-z0-9\-_%.]+/i,
  youtube: /https?:\/\/(?:www\.)?youtube\.com\/(?:@|c\/|channel\/|user\/)[A-Za-z0-9_\-]+/i,
  goodreads: /https?:\/\/(?:www\.)?goodreads\.com\/author\/show\/[^"'\s<]+/i,
  substack: /https?:\/\/[A-Za-z0-9\-]+\.substack\.com/i,
  bluesky: /https?:\/\/(?:www\.)?bsky\.app\/profile\/[A-Za-z0-9_.\-]+/i,
  threads: /https?:\/\/(?:www\.)?threads\.net\/@[A-Za-z0-9_.]+/i,
  patreon: /https?:\/\/(?:www\.)?patreon\.com\/[A-Za-z0-9_\-]+/i,
};


function normalizeLabel(text) {
  return String(text || "").toLowerCase().replace(/[:\s]+$/g, "").replace(/\s+/g, " ").trim();
}

/**
 * Label/value pairs, however the page marks them up.
 *
 * Covers definition lists, tables, and the very common pattern of two adjacent
 * inline elements where the first names the field.
 */
export function labelledFields(doc = document) {
  const found = {};
  const record = (label, value) => {
    const key = FIELD_LABELS[normalizeLabel(label)];
    const text = cleanText(value);
    if (key && text && !found[key]) found[key] = text;
  };

  for (const dt of doc.querySelectorAll("dt")) {
    const dd = dt.nextElementSibling;
    if (dd && dd.tagName === "DD") record(readableText(dt), readableText(dd));
  }
  for (const th of doc.querySelectorAll("th")) {
    const td = th.nextElementSibling;
    if (td && td.tagName === "TD") record(readableText(th), readableText(td));
  }
  // Two adjacent inline elements, the first naming the field.
  for (const node of doc.querySelectorAll("span, div, strong, b, label, p")) {
    const label = readableText(node);
    if (!label || label.length > 40) continue;
    if (!FIELD_LABELS[normalizeLabel(label)]) continue;
    const sibling = node.nextElementSibling;
    if (sibling) record(label, readableText(sibling));
  }
  // "Label: value" written as plain text.
  const bodyText = readableText(doc.body);
  for (const label of Object.keys(FIELD_LABELS)) {
    if (found[FIELD_LABELS[label]]) continue;
    const pattern = new RegExp(`${label}\\s*[:\\-–]\\s*([^\\n·|]{2,120})`, "i");
    const match = bodyText.match(pattern);
    if (match) record(label, match[1]);
  }
  return found;
}

/** Counts a book page publishes about its own reception. */
export function bookStatistics(doc = document) {
  const text = readableText(doc.body);
  const grab = (pattern) => {
    const match = text.match(pattern);
    return match ? parseCount(match[1]) : null;
  };
  return {
    ratingsCount: grab(/([\d,]+)\s*ratings?\b/i),
    reviewsCount: grab(/([\d,]+)\s*reviews?\b/i),
    // "voters" on a poll or listopia entry, distinct from ratings.
    votersCount: grab(/([\d,]+)\s*(?:voters?|votes?)\b/i),
    viewsCount: grab(/([\d,]+)\s*views?\b/i),
    wantToReadCount: grab(/([\d,]+)\s*(?:people\s*)?want to read\b/i),
    currentlyReadingCount: grab(/([\d,]+)\s*(?:people\s*)?(?:are\s*)?currently reading\b/i),
    editionsCount: grab(/([\d,]+)\s*editions?\b/i),
  };
}

const HEADING_TAGS = /^(?:H[1-6]|LEGEND|SUMMARY|STRONG|B|DT)$/;
const BIO_LABEL = /^\s*(?:about the author|about author|about the writer|meet the author|the author)\s*[:\-–]?\s*/i;

/**
 * The element actually holding the bio.
 *
 * A match can be either a heading ("<h3>About the author</h3>") or the section
 * that starts with those words, and the two need opposite treatment: for a
 * heading the content is around it, for a section it is inside it. Taking the
 * next sibling in both cases lands on whatever follows the section — often the
 * reviews — and reads that as the biography.
 */
function containerFor(heading) {
  if (!HEADING_TAGS.test(heading.tagName)) return heading;
  const parent = heading.parentElement;
  // The parent keeps the bio together with the author's links; fall back to the
  // next block when the parent is really the whole page.
  if (parent && readableText(parent).length < 6000) return parent;
  return heading.nextElementSibling || parent;
}

function cleanBio(text) {
  return cleanText(String(text || "").replace(BIO_LABEL, ""));
}

/** The "about the author" block, wherever the page puts it. */
export function authorSection(doc = document) {
  const headings = [...doc.querySelectorAll("h1,h2,h3,h4,h5,legend,summary,strong,[class*='author' i]")];
  for (const heading of headings) {
    const text = readableText(heading);
    if (!/^(about the author|about author|about the writer|meet the author|the author)\b/i.test(text)) {
      continue;
    }
    const container = containerFor(heading);
    if (!container) continue;
    const bio = cleanBio(readableText(container));
    if (bio && bio.length > 40) return { bio: bio.slice(0, 4000), node: container };
  }
  const fallback = doc.querySelector("[class*='authorBio' i], [class*='author-bio' i], [id*='authorBio' i]");
  if (fallback) {
    const bio = cleanBio(readableText(fallback));
    if (bio && bio.length > 40) return { bio: bio.slice(0, 4000), node: fallback };
  }
  return { bio: null, node: null };
}

/** Author links: their own site, their profiles, and any address they publish. */
export function authorLinks(doc = document, scope = null) {
  const socials = {};
  const root = scope || doc;
  const html = root.innerHTML || "";
  const hrefs = [...root.querySelectorAll("a[href]")].map((a) => a.getAttribute("href") || "");
  const haystack = hrefs.join("\n") + "\n" + html;

  for (const [network, pattern] of Object.entries(SOCIAL_PATTERNS)) {
    const match = haystack.match(pattern);
    if (match) socials[network] = match[0];
  }

  let website = null;
  const pageUrl = doc.location?.href || "";
  for (const href of hrefs) {
    if (!/^https?:\/\//i.test(href)) continue;
    if (isPlatformHost(href)) continue;
    // A link back into the same site is navigation, not the author's own site.
    if (pageUrl && isSameSite(href, pageUrl)) continue;
    website = href;
    break;
  }

  const emails = [];
  for (const link of root.querySelectorAll('a[href^="mailto:"]')) {
    const address = (link.getAttribute("href") || "").slice(7).split("?")[0].trim().toLowerCase();
    if (address.includes("@") && !emails.includes(address)) emails.push(address);
  }
  return { socials, website, emails };
}

/** A sample of review text, capped so a spreadsheet cell stays usable. */
export function communityReviews(doc = document, limit = 5) {
  const seen = new Set();
  const reviews = [];
  const containers = doc.querySelectorAll(
    "[class*='review' i], [id*='review' i], article[class*='comment' i]",
  );
  for (const node of containers) {
    if (node.querySelector("[class*='review' i]")) continue; // prefer the innermost
    const text = readableText(node);
    if (!text || text.length < 60 || text.length > 3000) continue;
    const key = text.slice(0, 60);
    if (seen.has(key)) continue;
    seen.add(key);
    const ratingMatch = text.match(/([\d.]+)\s*(?:out of|\/)\s*5/i);
    reviews.push({
      text: text.slice(0, 600),
      rating: ratingMatch ? Number(ratingMatch[1]) : null,
    });
    if (reviews.length >= limit) break;
  }
  return reviews;
}

/** Link to the editions list, where a site publishes one. */
export function moreEditionsUrl(doc = document, pageUrl = "") {
  for (const link of doc.querySelectorAll("a[href]")) {
    const text = readableText(link);
    if (!/\b(all|more)\s+editions?\b|\beditions?\b/i.test(text)) continue;
    try {
      return new URL(link.getAttribute("href"), pageUrl).href;
    } catch {
      return null;
    }
  }
  return null;
}

/** Everything the detail page adds beyond the basic record. */
export function extractDetail(doc = document, pageUrl = "") {
  const fields = labelledFields(doc);
  const stats = bookStatistics(doc);
  const author = authorSection(doc);
  // Only harvest author links from inside an author block. Falling back to the
  // whole page would collect the publisher's inbox and the site's own profiles
  // and file them under the author, which is worse than leaving them empty.
  const links = author.node
    ? authorLinks(doc, author.node)
    : { socials: {}, website: null, emails: [] };
  const reviews = communityReviews(doc);

  return {
    availableOn: availablePlatforms(doc, pageUrl),
    originalTitle: fields.originalTitle || null,
    edition: fields.edition || null,
    series: fields.series || null,
    awards: fields.awards || null,
    translator: fields.translator || null,
    firstPublished: fields.firstPublished || null,
    expectedPublication: fields.expectedPublication || null,
    labelledLanguage: fields.language || null,
    labelledPublisher: fields.publisher || null,
    labelledPageCount: toNumber(fields.pageCount),
    labelledPublishDate: fields.publishDate || fields.launchDate || null,
    labelledGenres: fields.genresText || null,
    statistics: stats,
    authorBio: author.bio,
    authorWebsite: links.website,
    authorSocials: links.socials,
    authorEmails: links.emails,
    reviews,
    moreEditionsUrl: moreEditionsUrl(doc, pageUrl),
  };
}

// Retailers and reading platforms, by host. A book page usually links to
// wherever it can be bought, and that list says a lot about how a title is
// being distributed — a single Amazon link reads very differently from a spread
// across ten stores.
const STORE_HOSTS = [
  [/(^|\.)amazon\./i, "Amazon"],
  [/(^|\.)audible\./i, "Audible"],
  [/(^|\.)barnesandnoble\.com/i, "Barnes & Noble"],
  [/(^|\.)bn\.com/i, "Barnes & Noble"],
  [/(^|\.)kobo\.com/i, "Kobo"],
  [/books\.apple\.com|itunes\.apple\.com/i, "Apple Books"],
  [/play\.google\.com\/store\/books/i, "Google Play Books"],
  [/(^|\.)bookshop\.org/i, "Bookshop.org"],
  [/(^|\.)waterstones\.com/i, "Waterstones"],
  [/(^|\.)blackwells\.co\.uk/i, "Blackwell's"],
  [/(^|\.)foyles\.co\.uk/i, "Foyles"],
  [/(^|\.)whsmith\.co\.uk/i, "WHSmith"],
  [/(^|\.)wordery\.com/i, "Wordery"],
  [/(^|\.)hive\.co\.uk/i, "Hive"],
  [/(^|\.)booktopia\.com/i, "Booktopia"],
  [/(^|\.)booksamillion\.com/i, "Books-A-Million"],
  [/(^|\.)thriftbooks\.com/i, "ThriftBooks"],
  [/(^|\.)indiebound\.org/i, "IndieBound"],
  [/(^|\.)libro\.fm/i, "Libro.fm"],
  [/(^|\.)scribd\.com|(^|\.)everand\.com/i, "Scribd/Everand"],
  [/(^|\.)smashwords\.com/i, "Smashwords"],
  [/(^|\.)goodreads\.com/i, "Goodreads"],
  [/(^|\.)librarything\.com/i, "LibraryThing"],
  [/(^|\.)storygraph\.com|app\.thestorygraph\.com/i, "StoryGraph"],
  [/(^|\.)netgalley\.com/i, "NetGalley"],
  [/(^|\.)wattpad\.com/i, "Wattpad"],
  [/(^|\.)lulu\.com/i, "Lulu"],
  [/(^|\.)ingramspark\.com/i, "IngramSpark"],
  [/(^|\.)draft2digital\.com|books2read\.com/i, "Draft2Digital"],
  [/(^|\.)target\.com/i, "Target"],
  [/(^|\.)walmart\.com/i, "Walmart"],
  [/(^|\.)waterstones\.com/i, "Waterstones"],
];

/**
 * Where this book can be had.
 *
 * Read from the outbound links a book page publishes, which is how a page shows
 * its buy buttons. The site being browsed is listed first, since it is itself a
 * platform the book appears on.
 */
export function availablePlatforms(doc = document, pageUrl = "") {
  const found = [];
  const add = (name) => {
    if (name && !found.includes(name)) found.push(name);
  };

  // The page you are on counts.
  try {
    const host = new URL(pageUrl).hostname;
    const known = STORE_HOSTS.find(([pattern]) => pattern.test(host));
    if (known) add(known[1]);
    else {
      const label = doc.querySelector('meta[property="og:site_name"]')?.getAttribute("content");
      add(cleanText(label));
    }
  } catch { /* a malformed page URL is not worth failing over */ }

  for (const link of doc.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href") || "";
    if (!/^https?:\/\//i.test(href)) continue;
    let host;
    try {
      host = new URL(href).hostname;
    } catch {
      continue;
    }
    const match = STORE_HOSTS.find(([pattern]) => pattern.test(host) || pattern.test(href));
    if (match) add(match[1]);
  }
  return found;
}
