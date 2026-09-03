/**
 * Finding the next page of a listing.
 *
 * Sites label this differently — "next", "next month", "older", "»", an arrow
 * glyph, or nothing but an aria-label — so several signals are tried in order of
 * how much they can be trusted. The `rel="next"` relation is a standard and is
 * believed outright; link text is a guess and is checked last.
 */

import { readableText } from "./normalize.js";

// "Next" across the languages a book site is plausibly published in, plus the
// glyphs used when there is no word at all.
const NEXT_WORDS = [
  "next", "next page", "next month", "older", "older posts", "more", "forward",
  "siguiente", "próxima", "proxima", "próximo", "suivant", "suivante", "weiter",
  "nächste", "nachste", "volgende", "næste", "naeste", "nästa", "nasta",
  "seuraava", "successivo", "avanti", "seguinte", "dalej", "další", "dalsi",
  "следующая", "следующий", "далее", "التالي", "التالية", "下一页", "次へ",
  "다음", "sonraki", "tiếp", "berikutnya", "selanjutnya",
];
const NEXT_GLYPHS = /^[\s]*(?:›+|»+|→+|▶+|>+|>>|➔|➜|⟩+)[\s]*$/;

// Words that look like "next" but move somewhere else entirely.
const NOT_NEXT = /\b(prev|previous|back|first|last|top|home|older comments)\b/i;

function normalizeUrl(url) {
  try {
    const parsed = new URL(url);
    parsed.hash = "";
    return parsed.href;
  } catch {
    return String(url || "");
  }
}

function candidateFrom(link, pageUrl) {
  const href = link.getAttribute("href");
  if (!href || href.startsWith("#") || /^javascript:/i.test(href)) return null;
  try {
    const url = new URL(href, pageUrl);
    if (url.origin !== new URL(pageUrl).origin) return null;
    if (normalizeUrl(url.href) === normalizeUrl(pageUrl)) return null;
    return url.href;
  } catch {
    return null;
  }
}

/**
 * The URL of the next listing page, or null.
 *
 * `visited` prevents the common trap where the last page links back to the
 * first, which would otherwise loop until the page limit is spent.
 */
export function findNextPage(doc = document, pageUrl = location.href, visited = new Set()) {
  const seen = new Set([...visited].map(normalizeUrl));
  const accept = (url) => (url && !seen.has(normalizeUrl(url)) ? url : null);

  // 1. The standard relation, in the head or on a link.
  for (const node of doc.querySelectorAll('link[rel~="next" i], a[rel~="next" i]')) {
    const href = node.getAttribute("href");
    if (!href) continue;
    try {
      const url = new URL(href, pageUrl).href;
      const good = accept(url);
      if (good) return good;
    } catch { /* malformed */ }
  }

  // 2. Marked up as pagination, by class or aria-label.
  const marked = doc.querySelectorAll(
    "a[class*='next' i], a[aria-label*='next' i], [class*='pagination' i] a[class*='next' i], " +
    "[class*='pager' i] a[class*='next' i], a[title*='next' i]",
  );
  for (const link of marked) {
    if (NOT_NEXT.test(link.className || "") || NOT_NEXT.test(link.getAttribute("aria-label") || "")) {
      continue;
    }
    const good = accept(candidateFrom(link, pageUrl));
    if (good) return good;
  }

  // 3. Link text, including bare glyphs.
  for (const link of doc.querySelectorAll("a[href]")) {
    const text = (readableText(link) || "").toLowerCase().replace(/\s+/g, " ").trim();
    const label = (link.getAttribute("aria-label") || "").toLowerCase();
    const subject = text || label;
    if (!subject || subject.length > 24) continue;
    if (NOT_NEXT.test(subject)) continue;
    const matches =
      NEXT_WORDS.includes(subject) ||
      NEXT_GLYPHS.test(subject) ||
      NEXT_WORDS.some((word) => subject === `${word} »` || subject === `${word} ›`);
    if (!matches) continue;
    const good = accept(candidateFrom(link, pageUrl));
    if (good) return good;
  }

  // 4. Nothing labelled: look for the same URL with its page number advanced.
  return accept(incrementedPage(doc, pageUrl, seen));
}

/**
 * A link identical to this page except the page number is one higher.
 *
 * Covers numbered pagination that offers only "1 2 3" with no next control.
 */
function incrementedPage(doc, pageUrl, seen) {
  let current;
  try {
    current = new URL(pageUrl);
  } catch {
    return null;
  }

  const patterns = [
    { kind: "query", keys: ["page", "p", "pg", "start", "offset"] },
    { kind: "path", regex: /\/page\/(\d+)/i },
  ];

  for (const pattern of patterns) {
    if (pattern.kind === "query") {
      for (const key of pattern.keys) {
        const value = current.searchParams.get(key);
        if (value === null || !/^\d+$/.test(value)) continue;
        const next = new URL(current.href);
        next.searchParams.set(key, String(Number(value) + 1));
        // Only trust it when the page actually offers that link.
        if (linkExists(doc, next.href, pageUrl) && !seen.has(normalizeUrl(next.href))) {
          return next.href;
        }
      }
    } else {
      const match = current.pathname.match(pattern.regex);
      if (!match) continue;
      const next = new URL(current.href);
      next.pathname = current.pathname.replace(
        pattern.regex,
        `/page/${Number(match[1]) + 1}`,
      );
      if (linkExists(doc, next.href, pageUrl) && !seen.has(normalizeUrl(next.href))) {
        return next.href;
      }
    }
  }
  return null;
}

function linkExists(doc, target, pageUrl) {
  const wanted = normalizeUrl(target);
  for (const link of doc.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href");
    if (!href) continue;
    try {
      if (normalizeUrl(new URL(href, pageUrl).href) === wanted) return true;
    } catch { /* malformed */ }
  }
  return false;
}
