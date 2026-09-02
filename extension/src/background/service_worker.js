/**
 * Storage, deduplication, and the queue that visits book pages.
 *
 * The queue opens ONE background tab at a time, waits for the page to settle,
 * takes what the content script found, saves it and closes the tab before
 * moving on. Opening forty tabs at once would be faster for about ten seconds
 * and would then get the browser bogged down and the session rate-limited; a
 * paced single worker looks like a person reading, and it can be stopped.
 */

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
};

// Hosts that are somebody's profile, not an author's own website.
const NOT_A_PERSONAL_SITE =
  /(goodreads|amazon|facebook|instagram|twitter|x\.com|tiktok|linkedin|youtube|pinterest|wikipedia|barnesandnoble|bookshop\.org|kobo|apple\.com|google\.|substack\.com)/i;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function keyOf(record) {
  return record.isbn || `${(record.bookName || "").toLowerCase()}|${(record.author || "").toLowerCase()}`;
}

async function saveRecords(records) {
  const stored = (await chrome.storage.local.get(KEY))[KEY] || [];
  const index = new Map(stored.map((record, position) => [keyOf(record), position]));
  let added = 0;
  let enriched = 0;

  for (const record of records || []) {
    const key = keyOf(record);
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
  const profile = await withTab(link.url, options, async (tabId) => {
    const response = await ask(tabId, { type: "orynx:author" });
    if (!response) throw new Error("no response from the author page");
    return response.profile;
  });
  if (!profile) return 0;

  if (options.followAuthorWebsite && profile.authorWebsite && !profile.authorEmail) {
    const site = profile.authorWebsite;
    if (/^https?:\/\//i.test(site) && !NOT_A_PERSONAL_SITE.test(site)) {
      try {
        const found = await withTab(site, options, async (tabId) => {
          const response = await ask(tabId, { type: "orynx:contacts" }, 4, 600);
          const contacts = response?.contacts;
          if (!contacts) return null;
          return {
            email: (contacts.emails || [])[0]?.value || null,
            socials: contacts.socials || {},
          };
        });
        if (found?.email) profile.authorEmail = found.email;
        // Their own site often lists profiles the catalogue page did not.
        for (const [network, url] of Object.entries(found?.socials || {})) {
          if (!profile.authorSocials[network]) profile.authorSocials[network] = url;
        }
      } catch {
        // An unreachable personal site is not a failure of the author page.
      }
    }
  }

  return applyProfileToLibrary(profile, link.author);
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

  queueState.running = false;
  queueState.current = null;
  await publishState();
  return queueState;
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
