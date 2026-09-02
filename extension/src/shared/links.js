/**
 * Telling an author's page from an author's website.
 *
 * These are different things and they belong in different columns. A page is
 * the author's profile *on the site you are browsing* — a catalogue's author
 * listing, a Goodreads profile. A website is the author's own domain, which is
 * the one worth following for an address.
 *
 * Getting this wrong puts the same URL in both columns, which is what happens
 * when schema.org markup supplies `author.url` pointing back at the same site.
 */

import { registrableDomain } from "./normalize.js";

// Social networks and the big book platforms. None of these is an author's own
// site, however personal the profile on them may be.
const PLATFORM_HOSTS =
  /^(?:www\.|m\.|[a-z]{2,3}\.)?(?:facebook|instagram|twitter|x|tiktok|linkedin|youtube|pinterest|threads|bsky|mastodon|patreon|substack|medium|tumblr|reddit|wikipedia|wikimedia|goodreads|amazon|audible|barnesandnoble|bookshop|kobo|apple|google|smashwords|wattpad|librarything|storygraph)\./i;

export const AUTHOR_PAGE_PATH = /\/(?:author|authors|writer|writers|contributor|contributors|profile|people)\//i;

function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

export function isPlatformHost(url) {
  const host = hostOf(url);
  if (!host) return false;
  return PLATFORM_HOSTS.test(host) || PLATFORM_HOSTS.test(`www.${host}`);
}

export function isSameSite(url, pageUrl) {
  const a = registrableDomain(hostOf(url));
  const b = registrableDomain(hostOf(pageUrl));
  return Boolean(a && b && a === b);
}

/**
 * What kind of author link is this, seen from `pageUrl`?
 *
 *  - "page"    a profile on the site being browsed, or a platform profile
 *  - "website" the author's own domain
 *  - "social"  a social profile, which belongs in its own column
 *  - null      not usable
 */
export function classifyAuthorLink(url, pageUrl = "") {
  if (!url || !/^https?:\/\//i.test(url)) return null;
  if (isSameSite(url, pageUrl)) return "page";
  if (isPlatformHost(url)) {
    // A profile on a book platform still reads as an author page; a profile on
    // a social network is a social link and nothing more.
    return AUTHOR_PAGE_PATH.test(url) ? "page" : "social";
  }
  return "website";
}

/** The author's own site, or null when the candidate is a page or a profile. */
export function asAuthorWebsite(url, pageUrl = "") {
  return classifyAuthorLink(url, pageUrl) === "website" ? url : null;
}

/** The author's profile page, or null when the candidate is their own site. */
export function asAuthorPage(url, pageUrl = "") {
  return classifyAuthorLink(url, pageUrl) === "page" ? url : null;
}
