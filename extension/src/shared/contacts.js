/**
 * Contact harvesting: email, phone, WhatsApp and social profiles.
 *
 * Everything here comes from what a page publishes about itself. Nothing is
 * guessed, no address patterns are invented, and each value records where on the
 * page it was found so a deletion request can be answered later.
 */

import { cleanText, readableText, registrableDomain } from "./normalize.js";

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;

// Deliberately strict: a loose phone pattern matches ISBNs, prices and dates.
// Requires either an international prefix or conventional separators.
// The final group runs to six digits: a UK number is "+44 1223 370012", and
// stopping at four silently truncates it to something undiallable.
const PHONE_RE =
  /(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,4}[\s.-]\d{3,6}(?:[\s.-]\d{2,4})?/g;

const SOCIAL_PATTERNS = {
  linkedin: /https?:\/\/(?:[a-z]{2,3}\.)?linkedin\.com\/(?:in|company)\/[A-Za-z0-9\-_%.]+/i,
  instagram: /https?:\/\/(?:www\.)?instagram\.com\/[A-Za-z0-9_.]+/i,
  twitter: /https?:\/\/(?:www\.)?(?:twitter|x)\.com\/[A-Za-z0-9_]+/i,
  facebook: /https?:\/\/(?:www\.)?facebook\.com\/[A-Za-z0-9_.\-]+/i,
  youtube: /https?:\/\/(?:www\.)?youtube\.com\/(?:@|c\/|channel\/|user\/)[A-Za-z0-9_\-]+/i,
  tiktok: /https?:\/\/(?:www\.)?tiktok\.com\/@[A-Za-z0-9_.]+/i,
  goodreads: /https?:\/\/(?:www\.)?goodreads\.com\/author\/show\/[^"'\s<]+/i,
  bluesky: /https?:\/\/(?:www\.)?bsky\.app\/profile\/[A-Za-z0-9_.\-]+/i,
  substack: /https?:\/\/[A-Za-z0-9\-]+\.substack\.com/i,
  threads: /https?:\/\/(?:www\.)?threads\.net\/@[A-Za-z0-9_.]+/i,
};

// Shared inboxes belong to an organisation. On the site's own domain they are
// still the right address; on someone else's they are noise.
const GENERIC_LOCAL_PARTS = new Set([
  "info", "contact", "support", "admin", "hello", "sales", "office", "webmaster",
  "noreply", "no-reply", "help", "press", "media", "enquiries", "enquiry",
  "billing", "careers", "jobs", "abuse", "postmaster", "privacy", "legal",
]);

// Link text and hrefs that indicate the page a prospect should be contacted through.
const CONTACT_HINTS = [
  "contact", "submissions", "submission", "submit", "get in touch", "reach us",
  "write for us", "pitch", "query", "manuscript", "advertise", "work with us",
  "about us", "our team", "support",
];

function looksLikePhone(candidate) {
  const digits = candidate.replace(/\D/g, "");
  if (digits.length < 7 || digits.length > 15) return false;
  // ISBN-13s and years-plus-numbers routinely match loose phone patterns.
  if (/^97[89]\d{10}$/.test(digits)) return false;
  if (/^(19|20)\d{2}$/.test(digits)) return false;
  return true;
}

function normalizePhone(candidate) {
  const trimmed = candidate.trim().replace(/[\s.\-()]+$/g, "");
  return trimmed.replace(/\s{2,}/g, " ");
}

/** WhatsApp appears as wa.me links, api links, or a number labelled in text. */
export function extractWhatsApp(doc, html) {
  const found = new Set();
  for (const link of doc.querySelectorAll('a[href*="wa.me"], a[href*="whatsapp"]')) {
    const href = link.getAttribute("href") || "";
    const match = href.match(/(?:wa\.me\/|phone=|send\?phone=)(\+?\d{6,15})/i);
    if (match) found.add(match[1].startsWith("+") ? match[1] : `+${match[1]}`);
    else if (/whatsapp/i.test(href)) found.add(href);
  }
  // "WhatsApp: +234 801 234 5678" written as plain text.
  const labelled = html.match(/whatsapp[^0-9+]{0,20}(\+?[\d][\d\s.\-()]{6,18})/gi) || [];
  for (const hit of labelled) {
    const number = hit.match(/(\+?[\d][\d\s.\-()]{6,18})/);
    if (number && looksLikePhone(number[1])) found.add(normalizePhone(number[1]));
  }
  return [...found].slice(0, 3);
}

/**
 * The domain the site considers its own.
 *
 * Prefers the canonical URL over the address bar, so a page served from a
 * staging host, a CDN or a local mirror still recognises its own addresses.
 */
export function siteDomain(doc, pageUrl) {
  const declared =
    doc.querySelector('link[rel="canonical"]')?.getAttribute("href") ||
    doc.querySelector('meta[property="og:url"]')?.getAttribute("content");
  for (const candidate of [declared, pageUrl]) {
    if (!candidate) continue;
    try {
      return registrableDomain(new URL(candidate, pageUrl || undefined).hostname);
    } catch { /* try the next candidate */ }
  }
  return "";
}

export function extractContacts(doc = document, pageUrl = "") {
  const html = doc.documentElement ? doc.documentElement.innerHTML : "";
  const pageDomain = siteDomain(doc, pageUrl);

  const emails = new Map();
  for (const link of doc.querySelectorAll('a[href^="mailto:"]')) {
    const address = (link.getAttribute("href") || "").slice(7).split("?")[0].trim().toLowerCase();
    if (address.includes("@")) emails.set(address, "mailto link");
  }
  const bodyText = readableText(doc.body);
  for (const address of bodyText.match(EMAIL_RE) || []) {
    const lowered = address.toLowerCase();
    if (!emails.has(lowered)) emails.set(lowered, "page text");
  }

  const keptEmails = [];
  for (const [address, where] of emails) {
    const [local, domain] = address.split("@");
    const ownDomain = registrableDomain(domain) === pageDomain;
    // On the site's own domain every address is theirs, "hello@" included.
    if (ownDomain || !GENERIC_LOCAL_PARTS.has(local)) {
      keptEmails.push({ value: address, foundIn: where, ownDomain });
    }
  }

  const phones = new Set();
  for (const link of doc.querySelectorAll('a[href^="tel:"]')) {
    const number = decodeURIComponent((link.getAttribute("href") || "").slice(4)).trim();
    if (looksLikePhone(number)) phones.add(normalizePhone(number));
  }
  for (const candidate of bodyText.match(PHONE_RE) || []) {
    if (looksLikePhone(candidate)) phones.add(normalizePhone(candidate));
    if (phones.size >= 5) break;
  }

  const socials = {};
  const linkHrefs = [...doc.querySelectorAll("a[href]")].map((a) => a.getAttribute("href") || "");
  const haystack = linkHrefs.join("\n") + "\n" + html;
  for (const [network, pattern] of Object.entries(SOCIAL_PATTERNS)) {
    const match = haystack.match(pattern);
    if (match) socials[network] = match[0];
  }

  return {
    emails: keptEmails.slice(0, 8),
    phones: [...phones].slice(0, 5),
    whatsapp: extractWhatsApp(doc, html),
    socials,
    contactPages: findContactPages(doc, pageUrl),
  };
}

/** Links to the page a prospect should actually be approached through. */
export function findContactPages(doc = document, pageUrl = "") {
  const scored = [];
  for (const link of doc.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href") || "";
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) continue;
    const text = (link.textContent || "").trim().toLowerCase();
    const target = `${href.toLowerCase()} ${text}`;
    for (let i = 0; i < CONTACT_HINTS.length; i += 1) {
      if (target.includes(CONTACT_HINTS[i])) {
        try {
          // Earlier hints are more specific, so they rank higher.
          scored.push({ url: new URL(href, pageUrl).href, rank: i, label: text.slice(0, 60) });
        } catch { /* malformed href */ }
        break;
      }
    }
  }
  const seen = new Set();
  return scored
    .sort((a, b) => a.rank - b.rank)
    .filter((item) => (seen.has(item.url) ? false : seen.add(item.url)))
    .slice(0, 5);
}
