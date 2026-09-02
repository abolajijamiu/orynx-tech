/**
 * Text, ISBN, date and name normalisation.
 *
 * Ported from the Python pipeline so the extension and the backend agree on what
 * a title, an ISBN and an author name look like. If these two ever disagree, the
 * same book arrives twice under different identities.
 */

const WHITESPACE = /\s+/g;
const SPACE_BEFORE_PUNCT = /\s+([.,;:!?%)\]}])/g;
const SPACE_AFTER_OPEN = /([([{])\s+/g;
const LEADING_ARTICLES = ["the ", "a ", "an "];
const ROLE_SUFFIX = /,?\s*\b(phd|ph\.d|md|m\.d|jr|sr|ii|iii|iv|esq|mba|dds)\b\.?$/i;
const HONORIFIC = /\b(dr|mr|mrs|ms|prof|professor|sir|dame)\b\.?/gi;

export function cleanText(value) {
  if (!value) return null;
  let text = String(value).replace(/<[^>]+>/g, " ");
  const entities = { "&amp;": "&", "&#39;": "'", "&quot;": '"', "&nbsp;": " ", "&lt;": "<", "&gt;": ">" };
  for (const [entity, char] of Object.entries(entities)) text = text.split(entity).join(char);
  text = text.replace(WHITESPACE, " ").trim();
  text = text.replace(SPACE_BEFORE_PUNCT, "$1").replace(SPACE_AFTER_OPEN, "$1");
  return text || null;
}

function foldAccents(value) {
  return value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
}

export function normalizeTitle(title) {
  if (!title) return "";
  let text = foldAccents(String(title)).toLowerCase().replace(/[^a-z0-9 ]+/g, " ");
  text = text.replace(WHITESPACE, " ").trim();
  for (const article of LEADING_ARTICLES) {
    if (text.startsWith(article)) return text.slice(article.length);
  }
  return text;
}

export function normalizePerson(name) {
  if (!name) return "";
  let text = foldAccents(String(name)).trim().replace(ROLE_SUFFIX, "");
  if (text.includes(",")) {
    const [family, ...rest] = text.split(",");
    text = `${rest.join(",").trim()} ${family.trim()}`;
  }
  text = text.toLowerCase().replace(HONORIFIC, " ").replace(/[^a-z0-9 ]+/g, " ");
  return text.replace(WHITESPACE, " ").trim();
}

export function normalizeIsbn(value) {
  if (!value) return null;
  const digits = String(value).replace(/[^0-9Xx]/g, "").toUpperCase();
  if (digits.length === 13 && /^\d+$/.test(digits)) return digits;
  if (digits.length === 10) return digits;
  return null;
}

export function isbn10To13(isbn10) {
  if (!isbn10 || isbn10.length !== 10) return null;
  const core = "978" + isbn10.slice(0, 9);
  let total = 0;
  for (let i = 0; i < core.length; i += 1) total += Number(core[i]) * (i % 2 === 0 ? 1 : 3);
  return core + String((10 - (total % 10)) % 10);
}

/**
 * Parse the partial dates book pages publish. A bare year returns no day, rather
 * than inventing a 1 January that would distort any recency filter.
 */
export function parseDate(value) {
  if (!value) return { iso: null, year: null };
  const text = String(value).trim();
  if (!text) return { iso: null, year: null };
  if (/^\d{4}$/.test(text)) return { iso: null, year: Number(text) };

  const isoMatch = text.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (isoMatch) return { iso: isoMatch[0], year: Number(isoMatch[1]) };

  const parsed = Date.parse(text);
  if (!Number.isNaN(parsed)) {
    const date = new Date(parsed);
    return { iso: date.toISOString().slice(0, 10), year: date.getUTCFullYear() };
  }
  const yearMatch = text.match(/\b(1[5-9]\d{2}|20\d{2})\b/);
  return { iso: null, year: yearMatch ? Number(yearMatch[1]) : null };
}

const STRONG_SPLIT = /[;&|]|\band\b|\bwith\b/i;
const BYLINE_PREFIX = /^\s*(?:by|written by|author[:s]?)\s*[:\-]?\s*/i;

/** Split a byline into names. See the Python implementation for the reasoning. */
export function splitAuthors(value) {
  if (!value) return [];
  const text = String(value).replace(BYLINE_PREFIX, "").trim();
  if (!text) return [];
  const names = [];
  for (const chunk of text.split(STRONG_SPLIT)) {
    if (!chunk) continue;
    const piece = chunk.replace(/^[\s.,\-|]+|[\s.,\-|]+$/g, "");
    if (!piece) continue;
    const commas = (piece.match(/,/g) || []).length;
    if (commas === 1 && piece.replace(/,/g, " ").split(/\s+/).length <= 3) {
      names.push(piece); // "King, Stephen"
    } else {
      for (const part of piece.split(",")) {
        const name = part.replace(/^[\s.,\-]+|[\s.,\-]+$/g, "");
        if (name.length > 1) names.push(name);
      }
    }
  }
  return names;
}

/** Pull the first integer out of "1,234 reviews" or "Ratings: 87". */
export function parseCount(value) {
  if (value === null || value === undefined) return null;
  const match = String(value).replace(/[,\s]/g, "").match(/\d+/);
  return match ? Number(match[0]) : null;
}

export function toNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(String(value).replace(/[^0-9.]/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

export function registrableDomain(hostname) {
  const parts = String(hostname || "").toLowerCase().split(".").filter(Boolean);
  return parts.length >= 2 ? parts.slice(-2).join(".") : parts.join(".");
}

export function absoluteUrl(href, base) {
  try {
    return new URL(href, base).href;
  } catch {
    return null;
  }
}

/**
 * Readable text for an element.
 *
 * `textContent` concatenates without separators, so "<span>$12.99</span>
 * <p>1,204 reviews</p>" becomes "$12.991,204 reviews" — which corrupts every
 * number and destroys the word boundaries bylines depend on. `innerText` fixes
 * that but forces layout on every call, so this walks text nodes and joins them
 * with a space: same result, no reflow.
 */
export function readableText(node) {
  if (!node) return "";
  const doc = node.ownerDocument || (typeof document !== "undefined" ? document : null);
  if (!doc || typeof doc.createTreeWalker !== "function") {
    return cleanText(node.textContent) || "";
  }
  const walker = doc.createTreeWalker(node, 4 /* SHOW_TEXT */);
  const parts = [];
  let current = walker.nextNode();
  while (current) {
    const text = (current.nodeValue || "").trim();
    if (text) parts.push(text);
    current = walker.nextNode();
  }
  return cleanText(parts.join(" ")) || "";
}
