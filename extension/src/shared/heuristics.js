/**
 * DOM heuristics: for pages that declare no structured data at all.
 *
 * The hard case is a shop or catalogue listing, where a card is often nothing
 * but a cover image, a title and a bare author name — no ISBN, no "by", no
 * price. Requiring one of those rejects most real publisher listings, so
 * corroboration is context-aware: on a page already identified as a book
 * platform, a cover plus an author-shaped line is evidence enough.
 *
 * Still conservative in the other direction. A wrong record is worse than a
 * missing one, so a bare heading never becomes a book.
 */

import { cleanText, normalizeIsbn, normalizeTitle, parseCount, readableText, splitAuthors } from "./normalize.js";

const ISBN_RE = /\b(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx])\b/;
const REVIEWS_RE = /([\d,]+)\s*(?:customer\s*)?(?:reviews?|ratings?)\b/i;
const READERS_RE = /([\d,]+)\s*(?:readers?|reads|people are reading|currently reading)\b/i;
const PUBDATE_RE = /(?:published|publication date|release date|pub date|first published|on sale)\s*:?\s*([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]+ \d{4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{4}|\d{4})/i;
const LAUNCH_RE = /(?:launch(?:es|ing|ed)?|releases?|available)\s*(?:on|date)?\s*:?\s*([A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]+ \d{4}|\d{4}-\d{2}-\d{2})/i;
const PRICE_RE = /[$£€₦¥]\s?\d+(?:[.,]\d{2})?/;

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
  "Home", "Books", "Authors", "About", "Contact", "Submissions", "Features",
  "Login", "Sign", "Cart", "Search", "Menu", "Fiction", "Non", "Latest",
];
// Lowercase particles carried inside many names: Ngugi wa Thiongo, Ludwig van
// Beethoven, Ahmed ibn Fadlan. Without these the surname is silently truncated.
const PARTICLES = [
  "van", "von", "de", "del", "della", "der", "den", "da", "di", "du", "dos",
  "la", "le", "bin", "ibn", "al", "wa", "ter", "ten", "af", "av",
];

const NAME_TOKEN =
  `(?!(?:${STOP_WORDS.join("|")})\\b)(?:[A-Z]\\.|[A-Z][\\p{L}'’\\-]*[\\p{Ll}])`;
const NAME_PART = `(?:${NAME_TOKEN}|(?:${PARTICLES.join("|")})\\b)`;
const NAME = `${NAME_TOKEN}(?:\\s+${NAME_PART}){0,4}`;
const BYLINE_RE = new RegExp(
  `\\b(?:by|written by|author)\\s*:?\\s+(${NAME}(?:\\s*(?:,|and|&)\\s*${NAME})*)`,
  "u",
);
const TRAILING_PARTICLE = new RegExp(`\\s+(?:${PARTICLES.join("|")})$`, "i");
const BYLINE_PREFIX = /^\s*(?:by|written by|author[:s]?)\s*[:\-]?\s*/i;

// Paths that mean "this link points at a book", used to find cards on sites
// whose class names give nothing away.
export const BOOK_LINK_RE = /\/(?:book|books|title|titles|product|products|shop|catalog|catalogue|item|p)\/[^/]/i;

// Containers that usually hold one book each on a listing page.
const CARD_SELECTORS = [
  "article", "li.product", "div.product", ".book", ".book-card", ".book-item",
  ".product-item", ".product-card", ".entry", ".post", ".card",
  "[class*='book']", "[class*='product']",
];

const AUTHOR_SELECTORS = [
  '[itemprop="author"]', '[class*="author" i]', '[class*="byline" i]',
  '[class*="writer" i]', ".by", "[rel='author']",
];

const JOB_WORDS =
  /\b(partner|manager|director|head|officer|ceo|cto|coo|cfo|founder|consultant|engineer|designer|analyst|assistant|associate|executive|president|chair|lead|specialist|coordinator|administrator)\b/i;

const NAV_WORDS = new Set([
  "home", "books", "authors", "about", "about us", "contact", "submissions",
  "features", "login", "sign up", "cart", "search", "menu", "shop", "blog",
  "news", "fiction", "non-fiction", "more", "next", "previous", "read more",
]);

function textOf(node) {
  return readableText(node);
}

/** Is this a plausible personal name rather than a label or a sentence? */
export function looksLikePersonName(text) {
  const cleaned = (text || "").replace(BYLINE_PREFIX, "").trim().replace(/[.,;:]$/, "");
  if (cleaned.length < 3 || cleaned.length > 80) return false;
  if (NAV_WORDS.has(cleaned.toLowerCase())) return false;
  if (/\d/.test(cleaned)) return false;
  const words = cleaned.split(/\s+/);
  if (words.length < 2 || words.length > 6) return false;
  const nameish = words.filter(
    (word) => /^[\p{Lu}]/u.test(word) || PARTICLES.includes(word.toLowerCase()),
  ).length;
  // Allows "Colleen Quinn, PhD" and "COLLEEN QUINN" while rejecting prose.
  return nameish >= 2 && nameish / words.length >= 0.6;
}

/** Leaf-ish elements, i.e. those holding text rather than more structure. */
function textBlocks(node) {
  const blocks = [];
  for (const element of node.querySelectorAll("span, div, p, strong, b, em, td, h5, h6, li")) {
    if (element.querySelector("span, div, p, li")) continue;
    const text = textOf(element);
    if (text && text.length >= 2 && text.length <= 200) blocks.push(text);
  }
  return blocks;
}

/**
 * Find the author of a card, whether or not the page writes "by".
 *
 * Publisher listings usually print the name as bare text under the title, so
 * requiring a byline misses most of them. The unlabelled fallback only runs in a
 * book context, because on a team page "Managing Partner" is shaped exactly like
 * a name and would otherwise be captured as one.
 */
export function findAuthor(node, options = {}) {
  const { bookContext = false, exclude = null, allowUnlabelled = true } = options;

  for (const selector of AUTHOR_SELECTORS) {
    const found = node.querySelector(selector);
    const text = textOf(found);
    if (text && looksLikePersonName(text)) return text.replace(BYLINE_PREFIX, "").trim();
  }

  const match = textOf(node).match(BYLINE_RE);
  if (match) return match[1].replace(TRAILING_PARTICLE, "");

  if (bookContext && allowUnlabelled) {
    for (const text of textBlocks(node)) {
      if (exclude && text === exclude) continue;
      if (JOB_WORDS.test(text)) continue;
      if (looksLikePersonName(text)) return text;
    }
  }
  return null;
}

// Alt text that describes the image rather than naming the book.
const GENERIC_ALT =
  /^(image|photo|picture|cover|book cover|thumbnail|thumb|logo|icon|avatar|banner|placeholder|img)$/i;
// Suffixes sites append to an otherwise usable alt: "Hollow Bones book cover".
const ALT_SUFFIX = /\s*[-–—|:]?\s*(?:book\s*)?cover(?:\s*image)?$/i;

/**
 * The title as an image alt or link title attribute.
 *
 * Cover-only grids — Goodreads, most retailers — render the title inside the
 * artwork, so the card carries no readable text at all. The alt attribute is
 * where the title actually lives on those pages.
 */
function titleFromMedia(node) {
  for (const image of node.querySelectorAll("img[alt]")) {
    const raw = cleanText(image.getAttribute("alt"));
    if (!raw || GENERIC_ALT.test(raw)) continue;
    const title = raw.replace(ALT_SUFFIX, "").trim();
    if (title.length >= 2 && title.length <= 200 && !NAV_WORDS.has(title.toLowerCase())) {
      return title;
    }
  }
  for (const link of node.querySelectorAll("a[title]")) {
    const title = cleanText(link.getAttribute("title"));
    if (title && title.length >= 2 && title.length <= 200 && !NAV_WORDS.has(title.toLowerCase())) {
      return title;
    }
  }
  return null;
}

/** Headings, title classes and link text: the reliable places a title lives. */
function findTitleStrong(node) {
  for (const selector of ["h1", "h2", "h3", "h4", ".title", "[class*='title']", "a"]) {
    for (const candidate of node.querySelectorAll(selector)) {
      const text = textOf(candidate);
      if (!text || text.length < 2 || text.length > 200) continue;
      if (NAV_WORDS.has(text.toLowerCase())) continue;
      return text;
    }
  }
  return null;
}

/** The words in a URL's last path segment: "/shop/book/red-umbrellas" -> "red umbrellas". */
function slugWords(href) {
  const path = String(href || "").split("?")[0].split("#")[0].replace(/\/+$/, "");
  const last = path.split("/").pop() || "";
  return last.replace(/[-_]+/g, " ").replace(/\.(html?|php|aspx)$/i, "").trim();
}

/**
 * Title and author when neither is marked up.
 *
 * A grid card can be two capitalised lines with nothing to say which is the book
 * and which is the person. The link decides it: "/shop/book/red-umbrellas"
 * names the title. Failing that, listings put the title first.
 */
function fallbackTitleAndAuthor(node, link, bookContext) {
  const blocks = textBlocks(node).filter(
    (text) =>
      !NAV_WORDS.has(text.toLowerCase()) &&
      !PRICE_RE.test(text) &&
      !/^[\d\s.,]+$/.test(text),
  );
  if (!blocks.length) return { title: null, author: null };

  let title = null;
  const slug = link ? normalizeTitle(slugWords(link.getAttribute("href") || "")) : "";
  if (slug.length > 3) {
    title =
      blocks.find((text) => normalizeTitle(text) === slug) ||
      blocks.find((text) => {
        const normalized = normalizeTitle(text);
        return normalized.length > 3 && slug.startsWith(normalized);
      }) ||
      null;
  }
  if (!title) title = blocks[0];

  const author = bookContext
    ? blocks.find(
        (text) => text !== title && !JOB_WORDS.test(text) && looksLikePersonName(text),
      ) || null
    : null;
  return { title, author };
}

function bookLinkIn(node) {
  // The node itself is the link on cover-only tiles, where querySelectorAll —
  // which searches descendants only — would find nothing.
  if (node.tagName === "A" && node.getAttribute("href")) return node;
  for (const link of node.querySelectorAll("a[href]")) {
    if (BOOK_LINK_RE.test(link.getAttribute("href") || "")) return link;
  }
  return node.querySelector("a[href]");
}

/**
 * Pull a book out of one container.
 *
 * `bookContext` says whether the page is already known to be about books — from
 * the platform registry or the page's own wording. When it is, a cover plus an
 * author-shaped line is enough; when it is not, hard evidence is required.
 */
export function bookFromNode(node, pageUrl, bookContext = false) {
  // Not "no text means no book": a cover tile carries its title in the image
  // alt and no readable text at all. Only an implausibly large node is rejected.
  const text = textOf(node);
  if (text.length > 4000) return null;

  const link = bookLinkIn(node);
  const strongTitle = findTitleStrong(node);
  // With a marked-up title the author search can exclude it. Without one, both
  // are unlabelled and must be decided together, or the two get swapped.
  let author = findAuthor(node, {
    bookContext,
    exclude: strongTitle,
    allowUnlabelled: Boolean(strongTitle),
  });
  let title = strongTitle;
  if (!title) {
    const fallback = fallbackTitleAndAuthor(node, link, bookContext);
    title = fallback.title;
    if (!author) author = fallback.author;
  }
  // Last resort: the cover's alt text, for grids that render the title as art.
  if (!title) title = titleFromMedia(node);
  if (!title) return null;

  const isbnMatch = text.match(ISBN_RE);
  const priceMatch = text.match(PRICE_RE);
  const image = node.querySelector("img[src], img[data-src], img[srcset]");
  const hasBookLink = Boolean(link && BOOK_LINK_RE.test(link.getAttribute("href") || ""));

  const strong = Boolean(isbnMatch || priceMatch || text.match(BYLINE_RE));
  const weak = Boolean(author && (image || hasBookLink)) || Boolean(image && hasBookLink);
  if (!strong && !(bookContext && weak)) return null;
  // Never accept the title on its own, whatever the context.
  if (!strong && !author && !image) return null;

  const pubMatch = text.match(PUBDATE_RE);
  const launchMatch = text.match(LAUNCH_RE);
  const reviewsMatch = text.match(REVIEWS_RE);
  const readersMatch = text.match(READERS_RE);
  const ratingMatch = text.match(/([\d.]+)\s*(?:out of|\/)\s*5/i);

  let href = null;
  if (link) {
    try {
      href = new URL(link.getAttribute("href"), pageUrl).href;
    } catch { /* malformed href */ }
  }

  return {
    title,
    subtitle: null,
    authors: author
      ? splitAuthors(author).map((name) => ({ name, url: null, description: null }))
      : [],
    isbn: isbnMatch ? normalizeIsbn(isbnMatch[1]) : null,
    publisher: null,
    publishedDate: pubMatch ? pubMatch[1] : null,
    launchDate: launchMatch ? launchMatch[1] : null,
    description: null,
    language: null,
    pageCount: null,
    coverUrl: image
      ? image.getAttribute("src") || image.getAttribute("data-src")
      : null,
    categories: [],
    averageRating: ratingMatch ? Number(ratingMatch[1]) : null,
    reviewsCount: reviewsMatch ? parseCount(reviewsMatch[1]) : null,
    ratingsCount: null,
    readersCount: readersMatch ? parseCount(readersMatch[1]) : null,
    price: priceMatch ? priceMatch[0] : null,
    currency: null,
    url: href || pageUrl,
    format: null,
    source: "heuristic",
  };
}

/**
 * A container holding several cover links is a row or grid, not a book.
 *
 * Listing pages group covers under a heading — a genre name, a shelf label —
 * and reading that container as one card turns the label into the title. The
 * individual links inside it are the books, and link discovery finds them.
 */
function isRowContainer(node) {
  let covers = 0;
  for (const link of node.querySelectorAll("a[href]")) {
    if (BOOK_LINK_RE.test(link.getAttribute("href") || "") && link.querySelector("img")) {
      covers += 1;
      if (covers >= 2) return true;
    }
  }
  return false;
}

/** Cards found by class name. */
function cardsBySelector(doc) {
  const selector = CARD_SELECTORS.join(",");
  const nodes = [...doc.querySelectorAll(selector)];
  // Drop a container only when it wraps something that is itself card-shaped,
  // which means holding a cover. Titles routinely match the selector and wrap a
  // link — "<h3 class='product-title'><a>…</a></h3>" — and letting that displace
  // its parent loses the card holding the author.
  return nodes.filter(
    (node) =>
      !isRowContainer(node) &&
      ![...node.querySelectorAll(selector)].some((child) => child.querySelector("img")),
  );
}

/**
 * Cards found by structure, for sites whose class names give nothing away.
 * Walks up from each book-shaped link to the smallest ancestor holding a cover.
 */
function cardsByLink(doc) {
  const found = new Set();
  for (const link of doc.querySelectorAll("a[href]")) {
    if (!BOOK_LINK_RE.test(link.getAttribute("href") || "")) continue;
    // A link wrapping its own cover is the card only when nothing around it adds
    // text — the pure cover grid, where walking up would swallow the whole row
    // and collapse many books into one. Where the container also holds a title
    // and author, that container is the card and we walk up to it as usual.
    if (link.querySelector("img")) {
      const parent = link.parentElement;
      // The link is the card when its container groups several covers (a genre
      // row or shelf, whose heading would otherwise become the title), or when
      // nothing around it adds text at all (a pure cover grid).
      if (!parent || isRowContainer(parent) || readableText(parent).length < 3) {
        found.add(link);
        continue;
      }
    }
    let node = link;
    for (let depth = 0; depth < 4 && node.parentElement; depth += 1) {
      const parent = node.parentElement;
      // Never climb into a container holding other books.
      if (isRowContainer(parent)) break;
      node = parent;
      if (node.querySelector("img") && readableText(node).length > 3) break;
    }
    if (node && node !== doc.body) found.add(node);
  }
  const cards = [...found];
  return cards.filter((node) => !cards.some((other) => other !== node && node.contains(other)));
}

export function extractHeuristicBooks(doc = document, pageUrl = "", options = {}) {
  const { limit = 200, bookContext = false } = options;
  // Keyed on title alone: the same book is often reached twice, once through its
  // card and once through the link inside it, and only one of those carries the
  // author. Keying on title plus author would keep both as separate books.
  const byTitle = new Map();

  const collect = (nodes) => {
    for (const node of nodes) {
      const book = bookFromNode(node, pageUrl, bookContext);
      if (!book) continue;
      const key = normalizeTitle(book.title);
      const existing = byTitle.get(key);
      if (existing) {
        // Keep whichever sighting knows more about the book.
        if (!existing.authors.length && book.authors.length) byTitle.set(key, book);
        continue;
      }
      byTitle.set(key, book);
      if (byTitle.size >= limit) return true;
    }
    return false;
  };

  if (collect(cardsBySelector(doc))) return [...byTitle.values()];
  // Structural discovery adds the cards class names missed.
  collect(cardsByLink(doc));

  // A detail page often has no card at all; read the page itself as one record.
  if (byTitle.size === 0 && doc.body) {
    const single = bookFromNode(doc.body, pageUrl, bookContext);
    if (single) return [single];
  }
  return [...byTitle.values()];
}

/** Why nothing was found — used by the panel's diagnostics. */
export function explainRejections(doc = document, pageUrl = "", bookContext = false) {
  const rejected = [];
  for (const node of [...cardsBySelector(doc), ...cardsByLink(doc)].slice(0, 60)) {
    if (bookFromNode(node, pageUrl, bookContext)) continue;
    const text = textOf(node);
    const title =
      findTitleStrong(node) || fallbackTitleAndAuthor(node, bookLinkIn(node), bookContext).title;
    rejected.push({
      title: title ? title.slice(0, 60) : null,
      reason: !title
        ? "no usable title in this container"
        : !findAuthor(node, { bookContext }) && !text.match(ISBN_RE) && !text.match(PRICE_RE)
          ? "title found, but no author, ISBN or price beside it"
          : "not enough corroborating evidence for this page context",
      sample: text.slice(0, 90),
    });
  }
  return rejected.slice(0, 12);
}


// Vocabulary that only appears on pages about books.
const BOOK_WORDS =
  /\b(isbn|paperback|hardback|hardcover|ebook|audiobook|novel|manuscript|synopsis|blurb|imprint|bookshop|our authors|book review)\b/i;

/**
 * Does this page look like it is about books, independent of the registry?
 *
 * This is what lets an unfamiliar site work on first contact: several
 * book-shaped links, or the vocabulary of publishing, is enough to relax the
 * evidence a listing card must carry.
 */
export function pageLooksBooky(doc = document, pageText = "") {
  let bookLinks = 0;
  for (const link of doc.querySelectorAll("a[href]")) {
    if (BOOK_LINK_RE.test(link.getAttribute("href") || "")) {
      bookLinks += 1;
      if (bookLinks >= 3) return true;
    }
  }
  return BOOK_WORDS.test(pageText.slice(0, 60000));
}
