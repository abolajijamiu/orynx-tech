/**
 * The in-page panel: what was found, filtered by how it can be contacted.
 *
 * Filtering lives here rather than only at export because the decision a user
 * makes on the page is "is this page worth saving at all", and that depends on
 * whether the records carry a usable contact route.
 */

import { COLUMNS, extractPage, matchesFilter } from "./extract.js";

const CHANNELS = [
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "whatsapp", label: "WhatsApp" },
  { key: "linkedin", label: "LinkedIn" },
  { key: "instagram", label: "Instagram" },
];

let records = [];
let filter = { channels: [], requireAll: false, minPriority: 0, tiers: [], query: "" };
let root = null;

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
  root.querySelector(".orynx-sub").textContent =
    `${records.length} found on this page · ${shown.length} match your filter`;

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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function selected() {
  const shown = visible();
  const boxes = [...root.querySelectorAll(".orynx-item input")];
  return boxes.filter((b) => b.checked).map((b) => shown[Number(b.dataset.index)]).filter(Boolean);
}

function toCsv(rows) {
  const escape = (value) => {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const header = COLUMNS.join(",");
  const body = rows.map((row) => COLUMNS.map((column) => escape(row[column])).join(","));
  return "﻿" + [header, ...body].join("\n");
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
      <div class="orynx-foot">
        <button class="orynx-btn" id="orynx-csv">Export CSV</button>
        <button class="orynx-btn primary" id="orynx-save">Save to library</button>
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
  root.querySelector("#orynx-csv").addEventListener("click", () => download(selected()));
  root.querySelector("#orynx-save").addEventListener("click", () => save(selected()));
}

export async function init() {
  const registry = await loadRegistry();
  records = extractPage(document, location.href, registry);
  // Expose for automated testing and for the popup to re-read.
  window.__orynxRecords = records;
  if (!document.body) return;
  buildUi();
  render();

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === "orynx:rescan") {
      records = extractPage(document, location.href, registry);
      window.__orynxRecords = records;
      render();
      respond({ ok: true, count: records.length });
    }
    return true;
  });
}
