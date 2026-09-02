/** Stores captured leads and deduplicates them across pages and sessions. */

const KEY = "orynx.leads";

function keyOf(record) {
  return record.isbn || `${(record.bookName || "").toLowerCase()}|${(record.author || "").toLowerCase()}`;
}

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message?.type === "orynx:save") {
    (async () => {
      const stored = (await chrome.storage.local.get(KEY))[KEY] || [];
      const seen = new Set(stored.map(keyOf));
      let added = 0;
      for (const record of message.records || []) {
        const key = keyOf(record);
        if (seen.has(key)) continue;
        seen.add(key);
        stored.push(record);
        added += 1;
      }
      await chrome.storage.local.set({ [KEY]: stored });
      respond({ ok: true, added, total: stored.length });
    })();
    return true;
  }
  if (message?.type === "orynx:list") {
    (async () => {
      respond({ ok: true, records: (await chrome.storage.local.get(KEY))[KEY] || [] });
    })();
    return true;
  }
  if (message?.type === "orynx:clear") {
    (async () => {
      await chrome.storage.local.set({ [KEY]: [] });
      respond({ ok: true });
    })();
    return true;
  }
  return false;
});
