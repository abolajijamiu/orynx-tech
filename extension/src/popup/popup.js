/** The saved-lead library: filter by contact channel, then export. */

const CHANNELS = [
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "whatsapp", label: "WhatsApp" },
  { key: "linkedin", label: "LinkedIn" },
  { key: "instagram", label: "Instagram" },
];

const COLUMNS = [
  "bookName", "author", "website", "company", "category", "country",
  "contactPage", "email", "linkedin", "instagram", "phone", "whatsapp",
  "services", "idealPitch", "priority", "tier",
  "launchDate", "publishDate", "readersCount", "reviewsCount", "ratingsCount",
  "averageRating", "isbn", "publisher", "price", "pageCount", "language",
  "format", "coverUrl", "sourceUrl", "extractedBy", "capturedAt",
];

let records = [];
let active = new Set();
let requireAll = false;

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

function render() {
  const shown = records.filter(matches);
  document.getElementById("summary").textContent =
    `${records.length} saved · ${shown.length} shown · ${countWith(records, "email")} with email, ` +
    `${countWith(records, "whatsapp")} with WhatsApp`;

  const list = document.getElementById("list");
  if (!shown.length) {
    list.innerHTML = `<div class="empty">${
      records.length ? "Nothing matches this filter." : "No saved leads yet.<br>Open a book page and press Save."
    }</div>`;
    return;
  }
  list.innerHTML = shown.map((record) => {
    const channels = CHANNELS.filter((c) => record[c.key]).map((c) => c.label).join(", ");
    return `<div class="row">
      <div class="name">${esc(record.bookName)}<span class="tier ${record.tier}">${record.tier} ${record.priority}</span></div>
      <div class="meta">${esc(record.author || "author unknown")} · ${esc(record.company || record.platformId || "")}</div>
      <div class="meta">${channels ? esc(channels) : "no contact route"} · ${esc(record.publishDate || "date unknown")}</div>
    </div>`;
  }).join("");
}

function countWith(rows, field) {
  return rows.filter((row) => Boolean(row[field])).length;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toCsv(rows) {
  const escape = (value) => {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return "﻿" + [
    COLUMNS.join(","),
    ...rows.map((row) => COLUMNS.map((column) => escape(row[column])).join(",")),
  ].join("\n");
}

async function boot() {
  const chips = document.getElementById("chips");
  chips.innerHTML = CHANNELS.map((c) => `<button class="chip" data-k="${c.key}">${c.label}</button>`).join("")
    + `<button class="chip" data-all="1">match all</button>`;

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

  document.getElementById("q").addEventListener("input", render);

  document.getElementById("export").addEventListener("click", () => {
    const blob = new Blob([toCsv(records.filter(matches))], { type: "text/csv;charset=utf-8" });
    chrome.downloads.download({
      url: URL.createObjectURL(blob),
      filename: `orynx-leads-${new Date().toISOString().slice(0, 10)}.csv`,
    });
  });

  document.getElementById("rescan").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id) await chrome.tabs.sendMessage(tab.id, { type: "orynx:rescan" });
  });

  document.getElementById("clear").addEventListener("click", async () => {
    if (!confirm("Delete every saved lead? This cannot be undone.")) return;
    await chrome.runtime.sendMessage({ type: "orynx:clear" });
    records = [];
    render();
  });

  const response = await chrome.runtime.sendMessage({ type: "orynx:list" });
  records = response?.records || [];
  render();
}

boot();
