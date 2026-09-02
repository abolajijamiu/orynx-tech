/**
 * Storage, deduplication, and the queue that visits book pages.
 *
 * The queue opens ONE background tab at a time, waits for the page to settle,
 * takes what the content script found, saves it and closes the tab before
 * moving on. Opening forty tabs at once would be faster for about ten seconds
 * and would then get the browser bogged down and the session rate-limited; a
 * paced single worker looks like a person reading, and it can be stopped.
 */

// Static import, not dynamic: a service worker forbids import() at runtime, and
// the manifest declares this worker as a module so a top-level import is fine.
import { classifyAuthorLink, isPlatformHost } from "../shared/links.js";

const KEY = "orynx.leads";
const QUEUE_KEY = "orynx.queue";

const DEFAULTS = {
  delayMs: 2500,      // pause between pages
  settleMs: 1200,     // let late-rendering content arrive before asking
  loadTimeoutMs: 30000,
  closeTabs: true,
  // After an author page names the author's own site, visit it too: that is
  // where a published address is most often found, and rarely anywhere else.
  followAuthorWebsite: true,
  // And, on that site, its contact page — the one place an address is close to
  // guaranteed when the homepage does not carry one.
  followContactPage: true,
  // Run the author stage automatically once the books are done.
  chainAuthors: true,
};



const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function titleKey(title) {
  return String(title || "")
    .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim()
    .replace(/^(?:the|a|an) /, "");
}

/**
 * The identity of a book in the library.
 *
 * Keyed on ISBN, or failing that the title alone — deliberately not the title
 * plus author. A listing often yields a title with no author and the detail
 * page then supplies one, and including the author in the key files those as
 * two different books.
 */
function keyOf(record) {
  return record.isbn ? `i:${record.isbn}` : `t:${titleKey(record.bookName)}`;
}

function nameKey(name) {
  if (!name) return "";
  let text = String(name).normalize("NFKD").replace(/[\u0300-\u036f]/g, "").trim();
  if (text.includes(",")) {
    const [family, ...rest] = text.split(",");
    text = `${rest.join(",").trim()} ${family.trim()}`;
  }
  return text.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
}

async function saveRecords(records) {
  const stored = (await chrome.storage.local.get(KEY))[KEY] || [];
  const index = new Map(stored.map((record, position) => [keyOf(record), position]));
  let added = 0;
  let enriched = 0;

  for (const record of records || []) {
    let key = keyOf(record);

    // Two different books can share a title. When both sightings name an author
    // and the names differ, they are separate books and get separate rows.
    if (index.has(key)) {
      const candidate = stored[index.get(key)];
      const existingAuthor = nameKey(candidate.author);
      const incomingAuthor = nameKey(record.author);
      if (existingAuthor && incomingAuthor && existingAuthor !== incomingAuthor) {
        key = `${key}|${incomingAuthor}`;
      }
    }

    if (!index.has(key)) {
      stored.push(record);
      index.set(key, stored.length - 1);
      added += 1;
      continue;
    }
    // A detail page knows more than the listing that led to it, so let it fill
    // in the blanks on the row already saved rather than creating a second one.
    const position = index.get(key);
    const existing = stored[position];
    let changed = false;
    for (const [field, value] of Object.entries(record)) {
      if (value === null || value === undefined || value === "" ) continue;
      if (existing[field] === null || existing[field] === undefined || existing[field] === "") {
        existing[field] = value;
        changed = true;
      }
    }
    if (changed) enriched += 1;
  }

  await chrome.storage.local.set({ [KEY]: stored });
  return { added, enriched, total: stored.length };
}

// --------------------------------------------------------------------------- //
// Queue
// --------------------------------------------------------------------------- //

let queueState = {
  running: false,
  stopRequested: false,
  total: 0,
  done: 0,
  saved: 0,
  failed: 0,
  current: null,
  errors: [],
};

async function publishState() {
  await chrome.storage.local.set({ [QUEUE_KEY]: queueState });
  // Nothing may be listening; that is not an error.
  chrome.runtime.sendMessage({ type: "orynx:queue:progress", state: queueState }).catch(() => {});
}

function waitForComplete(tabId, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(ok);
    };
    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") finish(true);
    };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab && tab.status === "complete") finish(true);
    }).catch(() => finish(false));
    setTimeout(() => finish(false), timeoutMs);
  });
}

/** Ask the content script something, allowing for a slow start. */
async function ask(tabId, message, attempts = 5, gapMs = 700) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, message);
      if (response?.ok) return response;
    } catch {
      // Content script not ready yet, or the page disallows injection.
    }
    await sleep(gapMs);
  }
  return null;
}

async function askForRecords(tabId) {
  const response = await ask(tabId, { type: "orynx:records" });
  return response ? response.records || [] : null;
}

/** Open a page, run `read` against its tab, and always clean the tab up. */
async function withTab(url, options, read) {
  let tab = null;
  try {
    tab = await chrome.tabs.create({ url, active: false });
    await waitForComplete(tab.id, options.loadTimeoutMs);
    await sleep(options.settleMs);
    return await read(tab.id);
  } finally {
    if (tab?.id && options.closeTabs) await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function processLink(link, options) {
  return withTab(link.url, options, async (tabId) => {
    const records = await askForRecords(tabId);
    if (records === null) throw new Error("no response from the page");
    if (!records.length) return 0;
    const result = await saveRecords(records);
    return result.added + result.enriched;
  });
}

/**
 * Read an author page, optionally follow the author's own site for an address,
 * then write what was learned onto every book by that author.
 */
async function processAuthor(link, options) {
  // A book's author link is sometimes a profile on the site and sometimes the
  // author's own website. Ask for both, so either shape yields something.
  const read = await withTab(link.url, options, async (tabId) => {
    const response = await ask(tabId, { type: "orynx:author" });
    if (!response) throw new Error("no response from the author page");
    const contacts = await ask(tabId, { type: "orynx:contacts" }, 2, 400);
    return { profile: response.profile, contacts: contacts?.contacts || null };
  });

  let profile = read.profile;
  if (!profile) {
    // Not a profile page. If it carries contact details it is very likely the
    // author's own site, which is exactly what the next hop would look for.
    const contacts = read.contacts;
    const hasSomething =
      contacts && ((contacts.emails || []).length || Object.keys(contacts.socials || {}).length);
    if (!hasSomething) return 0;
    // This URL was not a profile page. Whether it is the author's own site or a
    // profile elsewhere decides which column it belongs in.
    const kind = classifyAuthorLink(link.url, link.url);
    profile = {
      authorName: link.author || null,
      authorNameKey: null,
      authorPageUrl: kind === "page" ? link.url : null,
      authorBio: null,
      authorWebsite: kind === "website" ? link.url : null,
      authorEmail: null,
      authorPhone: null,
      authorSocials: {},
    };
    applyContacts(profile, contacts);
  }

  if (options.followAuthorWebsite && profile.authorWebsite && !profile.authorEmail) {
    await harvestFromSite(profile, profile.authorWebsite, options);
  }
  if (!profile.authorEmail && !Object.keys(profile.authorSocials || {}).length && !profile.authorBio) {
    return 0; // nothing was learned; do not touch the library
  }

  return applyProfileToLibrary(profile, link.author);
}

/** Read one page of the author's own site into the profile. */
async function readContacts(url, options) {
  return withTab(url, options, async (tabId) => {
    const response = await ask(tabId, { type: "orynx:contacts" }, 4, 600);
    return response?.contacts || null;
  });
}

/**
 * Take what an author's own website offers, following its contact page when the
 * landing page carries no address — which is the usual case, since sites put
 * the address one click away rather than on the front.
 */
async function harvestFromSite(profile, site, options) {
  if (!/^https?:\/\//i.test(site) || isPlatformHost(site)) return;

  let contacts = null;
  try {
    contacts = await readContacts(site, options);
  } catch {
    return; // an unreachable personal site is not a failure of the author page
  }
  if (!contacts) return;

  applyContacts(profile, contacts);

  if (profile.authorEmail || !options.followContactPage) return;
  const contactPage = (contacts.contactPages || [])[0]?.url;
  if (!contactPage || contactPage === site) return;
  try {
    const deeper = await readContacts(contactPage, options);
    if (deeper) applyContacts(profile, deeper);
  } catch {
    // The contact page is a best effort; the site's own details still stand.
  }
}

function applyContacts(profile, contacts) {
  if (!profile.authorEmail) {
    profile.authorEmail = (contacts.emails || [])[0]?.value || null;
  }
  if (!profile.authorPhone) {
    profile.authorPhone = (contacts.phones || [])[0] || (contacts.whatsapp || [])[0] || null;
  }
  // A personal site often lists profiles the catalogue page did not.
  for (const [network, url] of Object.entries(contacts.socials || {})) {
    if (!profile.authorSocials[network]) profile.authorSocials[network] = url;
  }
}

/** Fill gaps on every stored book credited to this author. */
async function applyProfileToLibrary(profile, fallbackName) {
  const stored = (await chrome.storage.local.get(KEY))[KEY] || [];
  const target = profile.authorNameKey || normalizeName(fallbackName);
  let updated = 0;

  const next = stored.map((record) => {
    const matchesUrl =
      record.authorUrl && profile.authorPageUrl &&
      record.authorUrl.split("?")[0] === profile.authorPageUrl.split("?")[0];
    const matchesName = target && normalizeName(record.author) === target;
    if (!matchesUrl && !matchesName) return record;
    const merged = applyProfile(record, profile);
    if (merged !== record) updated += 1;
    return merged;
  });

  if (updated) await chrome.storage.local.set({ [KEY]: next });
  return updated;
}

/** Kept in step with normalizePerson in the shared module. */
function normalizeName(name) {
  if (!name) return "";
  let text = String(name).normalize("NFKD").replace(/[\u0300-\u036f]/g, "").trim();
  text = text.replace(/,?\s*\b(phd|ph\.d|md|jr|sr|ii|iii|iv|esq|mba)\b\.?$/i, "");
  if (text.includes(",")) {
    const [family, ...rest] = text.split(",");
    text = `${rest.join(",").trim()} ${family.trim()}`;
  }
  return text.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
}

/** Gap-filling merge; returns the original object when nothing changed. */
function applyProfile(record, profile) {
  const updates = {
    authorBio: profile.authorBio,
    authorWebsite: profile.authorWebsite,
    email: profile.authorEmail,
    authorPhone: profile.authorPhone,
    authorPageUrl: profile.authorPageUrl,
    authorBorn: profile.authorBorn,
    authorLocation: profile.authorLocation,
    authorGenres: profile.authorGenres,
    authorBookCount: profile.authorBookCount,
    ...(profile.authorSocials || {}),
  };
  let changed = false;
  const merged = { ...record };
  for (const [field, value] of Object.entries(updates)) {
    if (!value) continue;
    if (merged[field] === null || merged[field] === undefined || merged[field] === "") {
      merged[field] = value;
      changed = true;
    }
  }
  return changed ? merged : record;
}

async function runQueue(links, overrides = {}, mode = "books") {
  const options = { ...DEFAULTS, ...overrides };
  queueState = {
    running: true, stopRequested: false, mode, total: links.length,
    done: 0, saved: 0, failed: 0, current: null, errors: [],
  };
  await publishState();

  for (const link of links) {
    if (queueState.stopRequested) break;
    queueState.current = link.title || link.url;
    await publishState();

    try {
      queueState.saved +=
        mode === "authors" ? await processAuthor(link, options) : await processLink(link, options);
    } catch (error) {
      queueState.failed += 1;
      if (queueState.errors.length < 10) {
        queueState.errors.push(`${link.title || link.url}: ${String(error.message || error)}`);
      }
    }
    queueState.done += 1;
    await publishState();

    if (!queueState.stopRequested) await sleep(options.delayMs);
  }

  if (mode === "books" && options.chainAuthors && !queueState.stopRequested) {
    const authors = await pendingAuthorLinks();
    if (authors.length) {
      const books = { done: queueState.done, saved: queueState.saved, failed: queueState.failed };
      await runQueue(authors, overrides, "authors");
      // Report the whole run, not just its second half.
      queueState.done += books.done;
      queueState.saved += books.saved;
      queueState.failed += books.failed;
      queueState.total += books.done;
      await publishState();
      return queueState;
    }
  }

  queueState.running = false;
  queueState.current = null;
  await publishState();
  return queueState;
}

/**
 * Author pages worth visiting: one per author, and only those not already read.
 * Re-reading an author because they wrote six books would be slower and ruder.
 */
async function pendingAuthorLinks() {
  const stored = (await chrome.storage.local.get(KEY))[KEY] || [];
  const seen = new Set();
  const links = [];
  for (const record of stored) {
    if (!record.authorUrl || record.authorPageUrl) continue;
    let url;
    try {
      url = new URL(record.authorUrl);
    } catch {
      continue;
    }
    const clean = url.origin + url.pathname;
    if (seen.has(clean)) continue;
    seen.add(clean);
    links.push({ url: clean, title: record.author || clean, author: record.author || null });
  }
  return links;
}

// --------------------------------------------------------------------------- //
// Messages
// --------------------------------------------------------------------------- //

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message?.type === "orynx:save") {
    saveRecords(message.records).then((result) => respond({ ok: true, ...result }));
    return true;
  }
  if (message?.type === "orynx:list") {
    chrome.storage.local.get(KEY).then((data) => respond({ ok: true, records: data[KEY] || [] }));
    return true;
  }
  if (message?.type === "orynx:clear") {
    chrome.storage.local.set({ [KEY]: [] }).then(() => respond({ ok: true }));
    return true;
  }
  if (message?.type === "orynx:queue:start") {
    if (queueState.running) {
      respond({ ok: false, error: "a run is already in progress" });
      return true;
    }
    // Answer immediately; the run reports progress as it goes.
    respond({ ok: true, total: (message.links || []).length });
    runQueue(message.links || [], message.options || {}, message.mode || "books");
    return true;
  }
  if (message?.type === "orynx:queue:stop") {
    queueState.stopRequested = true;
    respond({ ok: true });
    return true;
  }
  if (message?.type === "orynx:queue:status") {
    respond({ ok: true, state: queueState });
    return true;
  }
  return false;
});
