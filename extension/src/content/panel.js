/**
 * The in-page panel: what was found, filtered by how it can be contacted.
 *
 * Filtering lives here rather than only at export because the decision a user
 * makes on the page is "is this page worth saving at all", and that depends on
 * whether the records carry a usable contact route.
 */

import { collectAuthorLinks, collectBookLinks, diagnosePage, extractAuthor, extractPage, matchesFilter, toCsv } from "./extract.js";
import { extractContacts } from "../shared/contacts.js";

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

let records = [];
let filter = { channels: [], requireAll: false, minPriority: 0, tiers: [], query: "" };
let root = null;
let cachedRegistry = null;

async function loadRegistry() {
  try {
    const response = await fetch(chrome.runtime.getURL("src/shared/registry.json"));
    return await response.json();
  } catch {
    return null; // classification falls back to page content
  }
}

function visible() {
  return records.filter((record) => matchesFilter(record, filter));
}

function render() {
  if (!root) return;
  const shown = visible();
  root.querySelector("#orynx-count").textContent = String(shown.length);
  const withChannel = (field) => records.filter((record) => record[field]).length;
  root.querySelector(".orynx-sub").textContent =
    `${records.length} found · ${shown.length} shown · ` +
    `${withChannel("email")} email · ${withChannel("authorWebsite")} website · ` +
    `${withChannel("facebook")} facebook · ${withChannel("twitter")} X`;

  const list = root.querySelector(".orynx-list");
  if (!shown.length) {
    list.innerHTML = records.length
      ? `<div class="orynx-empty">No records match the filter.<br>Turn off a channel chip to see more.</div>`
      : `<div class="orynx-empty">No books detected on this page.<br>Try a book detail page or a catalogue listing.</div>`;
    return;
  }

  list.innerHTML = shown
    .map((record, index) => {
      const channels = CHANNELS.filter((c) => record[c.key]).map((c) => c.label);
      const bits = [
        record.author || "author unknown",
        record.publishDate || record.launchDate || "",
        record.reviewsCount !== null && record.reviewsCount !== undefined ? `${record.reviewsCount} reviews` : "",
        record.readersCount ? `${record.readersCount} readers` : "",
      ].filter(Boolean);
      return `
        <label class="orynx-item">
          <input type="checkbox" data-index="${index}" checked>
          <span style="flex:1">
            <span class="orynx-name">${escapeHtml(record.bookName)}
              <span class="orynx-tier orynx-${record.tier}">${record.tier} ${record.priority}</span>
            </span>
            <span class="orynx-meta">${escapeHtml(bits.join(" · "))}</span>
            <span class="orynx-meta">${
              channels.length
                ? `<span class="orynx-dot"></span>${escapeHtml(channels.join(", "))}`
                : "no contact route found"
            } · via ${escapeHtml(record.extractedBy)}</span>
          </span>
        </label>`;
    })
    .join("");
}

function showProgress(text) {
  const node = root?.querySelector("#orynx-progress");
  if (!node) return;
  node.hidden = false;
  node.textContent = text;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function selected() {
  const shown = visible();
  const boxes = [...root.querySelectorAll(".orynx-item input")];
  return boxes.filter((b) => b.checked).map((b) => shown[Number(b.dataset.index)]).filter(Boolean);
}

function download(rows) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `orynx-leads-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

async function save(rows) {
  const response = await chrome.runtime.sendMessage({ type: "orynx:save", records: rows });
  const button = root.querySelector("#orynx-save");
  button.textContent = response?.ok ? `Saved ${response.added}` : "Save failed";
  setTimeout(() => { button.textContent = "Save to library"; }, 2200);
}

function buildUi() {
  root = document.createElement("div");
  root.id = "orynx-root";
  root.innerHTML = `
    <button id="orynx-badge">Orynx <span id="orynx-count">0</span></button>
    <div id="orynx-panel">
      <div class="orynx-head">
        <div>
          <div class="orynx-title">Book leads on this page</div>
          <div class="orynx-sub"></div>
        </div>
        <button class="orynx-x" title="Close">×</button>
      </div>
      <div class="orynx-filters">
        ${CHANNELS.map((c) => `<button class="orynx-chip" data-channel="${c.key}">${c.label}</button>`).join("")}
        <label class="orynx-mode"><input type="checkbox" id="orynx-all"> match all</label>
      </div>
      <div class="orynx-list"></div>
      <div class="orynx-progress" id="orynx-progress" hidden></div>
      <div class="orynx-foot">
        <button class="orynx-btn" id="orynx-why" title="Explain what was and was not found">Why?</button>
        <button class="orynx-btn" id="orynx-links" title="Copy every book link on this page">Copy links</button>
        <button class="orynx-btn" id="orynx-csv">Export CSV</button>
        <button class="orynx-btn primary" id="orynx-save">Save</button>
      </div>
      <div class="orynx-foot">
        <button class="orynx-btn wide primary" id="orynx-crawl">Visit each book and save</button>
        <button class="orynx-btn" id="orynx-authors" title="Visit each author's page for bio, site and contacts">Authors</button>
        <button class="orynx-btn" id="orynx-stop" hidden>Stop</button>
      </div>
    </div>`;

  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = chrome.runtime.getURL("src/content/panel.css");
  document.documentElement.appendChild(style);
  document.body.appendChild(root);

  root.querySelector("#orynx-badge").addEventListener("click", () => root.classList.add("open"));
  root.querySelector(".orynx-x").addEventListener("click", () => root.classList.remove("open"));

  for (const chip of root.querySelectorAll(".orynx-chip")) {
    chip.addEventListener("click", () => {
      const channel = chip.dataset.channel;
      chip.classList.toggle("on");
      filter.channels = filter.channels.includes(channel)
        ? filter.channels.filter((c) => c !== channel)
        : [...filter.channels, channel];
      render();
    });
  }
  root.querySelector("#orynx-all").addEventListener("change", (event) => {
    filter.requireAll = event.target.checked;
    render();
  });
  root.querySelector("#orynx-why").addEventListener("click", async () => {
    const report = JSON.stringify(diagnosePage(document, location.href, cachedRegistry), null, 2);
    const list = root.querySelector(".orynx-list");
    list.innerHTML = `<pre class="orynx-diag">${escapeHtml(report)}</pre>`;
    try {
      await navigator.clipboard.writeText(report);
      root.querySelector("#orynx-why").textContent = "Copied";
    } catch {
      // Clipboard needs a user gesture and permission; the text is on screen anyway.
      root.querySelector("#orynx-why").textContent = "Select and copy";
    }
    setTimeout(() => { root.querySelector("#orynx-why").textContent = "Why?"; }, 2500);
  });

  root.querySelector("#orynx-links").addEventListener("click", async () => {
    const links = collectBookLinks(document, location.href);
    const button = root.querySelector("#orynx-links");
    if (!links.length) {
      button.textContent = "No links";
      setTimeout(() => { button.textContent = "Copy links"; }, 2000);
      return;
    }
    const text = links.map((link) => link.url).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = `Copied ${links.length}`;
    } catch {
      // Clipboard can be refused without a trusted gesture; show them instead.
      root.querySelector(".orynx-list").innerHTML =
        `<pre class="orynx-diag">${escapeHtml(text)}</pre>`;
      button.textContent = "Shown below";
    }
    setTimeout(() => { button.textContent = "Copy links"; }, 2500);
  });

  root.querySelector("#orynx-crawl").addEventListener("click", async () => {
    const links = collectBookLinks(document, location.href);
    if (!links.length) {
      showProgress("No book links found on this page.");
      return;
    }
    const proceed = confirm(
      `Visit ${links.length} book page(s), then each author, and save everything?\n\n` +
      "One background tab at a time: the book page, then the author's page, then " +
      "their own website and its contact page. Each opens, is read, saved and " +
      "closed, with a pause between. You can stop at any point.",
    );
    if (!proceed) return;
    await chrome.runtime.sendMessage({ type: "orynx:queue:start", links });
    root.querySelector("#orynx-stop").hidden = false;
    showProgress(`Starting on ${links.length} book page(s)…`);
  });

  root.querySelector("#orynx-authors").addEventListener("click", async () => {
    // Authors come from the whole library, not just this page: the point is to
    // enrich everything collected so far, and one author covers many books.
    const response = await chrome.runtime.sendMessage({ type: "orynx:list" });
    const links = collectAuthorLinks(response?.records || []);
    if (!links.length) {
      showProgress("No author pages found. Visit some book pages first — that is where author links come from.");
      return;
    }
    const proceed = confirm(
      `Visit ${links.length} author page(s) and fill in bio, website and contacts?\n\n` +
      "Each author is visited once, however many of their books you have saved. " +
      "Where an author names their own website, that is opened too, since a " +
      "published email address is rarely anywhere else.",
    );
    if (!proceed) return;
    await chrome.runtime.sendMessage({ type: "orynx:queue:start", mode: "authors", links });
    root.querySelector("#orynx-stop").hidden = false;
    showProgress(`Starting on ${links.length} author page(s)…`);
  });

  root.querySelector("#orynx-stop").addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "orynx:queue:stop" });
    showProgress("Stopping after the current page…");
  });

  root.querySelector("#orynx-csv").addEventListener("click", () => download(selected()));
  root.querySelector("#orynx-save").addEventListener("click", () => save(selected()));
}

function rescan() {
  records = extractPage(document, location.href, cachedRegistry);
  window.__orynxRecords = records;
  render();
  return records.length;
}

/**
 * Most shop and catalogue front-ends render their grid after document_idle, so
 * a single pass at load time finds nothing. This re-runs extraction when the
 * page changes, debounced, and gives up once the page settles — a permanent
 * observer on a busy site would cost more than it returns.
 */
function watchForLateContent() {
  let timer = null;
  let passes = 0;
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      passes += 1;
      const before = records.length;
      const after = rescan();
      if (passes >= 12 || (after > 0 && after === before)) observer.disconnect();
    }, 700);
  });
  observer.observe(document.body, { childList: true, subtree: true });
  // Stop watching regardless after a minute; nothing useful arrives later.
  setTimeout(() => observer.disconnect(), 60000);
}

export async function init() {
  cachedRegistry = await loadRegistry();
  records = extractPage(document, location.href, cachedRegistry);
  // Expose for automated testing and for the popup to re-read.
  window.__orynxRecords = records;
  if (!document.body) return;
  buildUi();
  render();
  watchForLateContent();

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === "orynx:queue:progress") {
      const state = message.state || {};
      const noun = state.mode === "authors" ? "author page" : "book page";
      if (state.running) {
        showProgress(
          `${state.done}/${state.total} ${noun}s visited · ${state.saved} updated` +
          (state.failed ? ` · ${state.failed} failed` : "") +
          (state.current ? ` · reading ${String(state.current).slice(0, 40)}` : ""),
        );
      } else {
        showProgress(
          `Finished: ${state.done}/${state.total} ${noun}s visited, ${state.saved} record(s) updated` +
          (state.failed ? `, ${state.failed} failed` : "") + ".",
        );
        if (root) root.querySelector("#orynx-stop").hidden = true;
      }
    }
    if (message?.type === "orynx:rescan") {
      rescan();
      respond({ ok: true, count: records.length, records });
    }
    if (message?.type === "orynx:records") {
      respond({ ok: true, count: records.length, records });
    }
    if (message?.type === "orynx:author") {
      respond({ ok: true, profile: extractAuthor(document, location.href) });
    }
    if (message?.type === "orynx:contacts") {
      // An author's own website is rarely a book page, so asking for book
      // records there returns nothing. Contacts are what that visit is for.
      respond({ ok: true, contacts: extractContacts(document, location.href) });
    }
    return true;
  });
}
