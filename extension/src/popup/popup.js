/**
 * Two views: what is on the page in front of you, and what you have saved.
 *
 * "This page" is the default, because someone opening the extension while
 * looking at a catalogue wants to see that catalogue — not an empty library.
 * Showing only saved leads made Rescan appear to do nothing, since its effect
 * was visible on the page badge and nowhere here.
 */

const CHANNELS = [
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "whatsapp", label: "WhatsApp" },
  { key: "authorWebsite", label: "Website" },
  { key: "linkedin", label: "LinkedIn" },
  { key: "instagram", label: "Instagram" },
  { key: "facebook", label: "Facebook" },
  { key: "twitter", label: "X" },
];

// The export shape lives with the extractor so the panel and the popup cannot
// drift apart; loaded lazily because a popup is not a content script.
let buildCsv = null;
async function csvFor(rows) {
  if (!buildCsv) {
    ({ toCsv: buildCsv } = await import(chrome.runtime.getURL("src/content/extract.js")));
  }
  return buildCsv(rows);
}

let view = "page";
let pageRecords = [];
let savedRecords = [];
let active = new Set();
let requireAll = false;

const current = () => (view === "page" ? pageRecords : savedRecords);

function matches(record) {
  const query = document.getElementById("q").value.trim().toLowerCase();
  if (query) {
    const haystack = [record.bookName, record.author, record.company, record.publisher]
      .filter(Boolean).join(" ").toLowerCase();
    if (!haystack.includes(query)) return false;
  }
  if (active.size) {
    const present = [...active].filter((channel) => Boolean(record[channel]));
    return requireAll ? present.length === active.size : present.length > 0;
  }
  return true;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function render() {
  const rows = current();
  const shown = rows.filter(matches);

  const where = view === "page" ? "on this page" : "saved";
  document.getElementById("summary").textContent =
    `${rows.length} ${where} · ${shown.length} shown · ` +
    `${countWith(rows, "email")} email · ${countWith(rows, "authorWebsite")} website · ` +
    `${countWith(rows, "phone")} phone · ${countWith(rows, "facebook")} facebook · ` +
    `${countWith(rows, "twitter")} X`;

  document.getElementById("save").style.display = view === "page" ? "" : "none";
  document.getElementById("clear").style.display = view === "saved" ? "" : "none";

  const list = document.getElementById("list");
  if (!shown.length) {
    list.innerHTML = `<div class="empty">${
      rows.length
        ? "Nothing matches this filter."
        : view === "page"
          ? "No books found on this page.<br>Try Rescan, or a book listing page."
          : "No saved leads yet.<br>Open a book page and press Save."
    }</div>`;
    return;
  }

  list.innerHTML = shown.map((record) => {
    const channels = CHANNELS.filter((c) => record[c.key]).map((c) => c.label).join(", ");
    return `<div class="row">
      <div class="name">${esc(record.bookName)}<span class="tier ${record.tier}">${record.tier} ${record.priority}</span></div>
      <div class="meta">${esc(record.author || "author unknown")} · ${esc(record.company || record.platformId || "")}</div>
      <div class="meta">${channels ? esc(channels) : "no contact route"} · ${esc(record.publishDate || "date unknown")}</div>
      ${record.authorWebsite ? `<div class="meta site">${esc(record.authorWebsite)}</div>` : ""}
    </div>`;
  }).join("");
}

function countWith(rows, field) {
  return rows.filter((row) => Boolean(row[field])).length;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function loadPageRecords(rescan = false) {
  const tab = await activeTab();
  if (!tab?.id) return [];
  try {
    const response = await chrome.tabs.sendMessage(tab.id, {
      type: rescan ? "orynx:rescan" : "orynx:records",
    });
    return response?.records || [];
  } catch {
    // No content script here: a chrome:// page, the web store, or a tab opened
    // before the extension was installed.
    return [];
  }
}

async function loadSaved() {
  const response = await chrome.runtime.sendMessage({ type: "orynx:list" });
  return response?.records || [];
}

function flash(id, text) {
  const button = document.getElementById(id);
  const original = button.textContent;
  button.textContent = text;
  setTimeout(() => { button.textContent = original; }, 2000);
}

async function boot() {
  const chips = document.getElementById("chips");
  chips.innerHTML =
    CHANNELS.map((c) => `<button class="chip" data-k="${c.key}">${c.label}</button>`).join("") +
    `<button class="chip" data-all="1">match all</button>`;

  chips.addEventListener("click", (event) => {
    const button = event.target.closest(".chip");
    if (!button) return;
    button.classList.toggle("on");
    if (button.dataset.all) {
      requireAll = button.classList.contains("on");
    } else {
      const key = button.dataset.k;
      active.has(key) ? active.delete(key) : active.add(key);
    }
    render();
  });

  document.querySelector(".tabs").addEventListener("click", async (event) => {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === tab));
    view = tab.dataset.view;
    if (view === "saved") savedRecords = await loadSaved();
    render();
  });

  document.getElementById("q").addEventListener("input", render);

  document.getElementById("rescan").addEventListener("click", async () => {
    pageRecords = await loadPageRecords(true);
    if (view !== "page") {
      view = "page";
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.view === "page"));
    }
    render();
    flash("rescan", `Found ${pageRecords.length}`);
  });

  document.getElementById("save").addEventListener("click", async () => {
    const rows = pageRecords.filter(matches);
    if (!rows.length) return flash("save", "Nothing to save");
    const response = await chrome.runtime.sendMessage({ type: "orynx:save", records: rows });
    savedRecords = await loadSaved();
    flash("save", response?.ok ? `Saved ${response.added}` : "Failed");
  });

  document.getElementById("export").addEventListener("click", async () => {
    const rows = current().filter(matches);
    if (!rows.length) return flash("export", "Nothing to export");
    const blob = new Blob([await csvFor(rows)], { type: "text/csv;charset=utf-8" });
    chrome.downloads.download({
      url: URL.createObjectURL(blob),
      filename: `orynx-leads-${new Date().toISOString().slice(0, 10)}.csv`,
    });
  });

  document.getElementById("clear").addEventListener("click", async () => {
    if (!confirm("Delete every saved lead? This cannot be undone.")) return;
    await chrome.runtime.sendMessage({ type: "orynx:clear" });
    savedRecords = [];
    render();
  });

  [pageRecords, savedRecords] = await Promise.all([loadPageRecords(), loadSaved()]);
  // Land on whichever view actually has something in it.
  if (!pageRecords.length && savedRecords.length) {
    view = "saved";
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.view === "saved"));
  }
  render();
}

boot();
