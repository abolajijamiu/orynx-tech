/**
 * Extension checks in real Chromium against local fixture pages.
 *
 * Self-contained: it serves tests/pages itself, so there is nothing to start
 * first and no network access involved.
 *
 * Two halves, because a content script runs in an isolated world that
 * page.evaluate cannot reach:
 *  - extraction is exercised by importing the modules into the page itself,
 *    against a real DOM;
 *  - the panel is exercised through the loaded extension, whose UI lives in the
 *    shared DOM and so is visible to assertions.
 */
import { chromium } from "playwright";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(".");
const EXT = path.join(ROOT, "extension");
const PAGES = path.join(ROOT, "tests", "pages");
const CHROME = process.env.CHROME_PATH || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

const MIME = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".css": "text/css" };

// Serve fixtures and the extension source from one origin, so the test page can
// import the modules without cross-origin restrictions.
let BASE = "";
const server = http.createServer((req, res) => {
  const url = decodeURIComponent(req.url.split("?")[0]);
  let name = url === "/" ? "index.html" : url;
  // Book detail links resolve to flat fixture files: /book/hollow-bones -> detail_book.html
  if (name.startsWith("/book/")) {
    const slug = name.slice(6).replace(/\/$/, "");
    name = slug === "hollow-bones" ? "/detail_book.html" : `/${slug}.html`;
  }
  if (name === "/author/marta-oyelaran") name = "/author_page_local.html";
  else if (name === "/author/marta-nomail") name = "/author_page_nomail.html";
  else if (name.startsWith("/author/")) name = "/author_page.html";
  if (name === "/site") name = "/author_site.html";
  if (name === "/nomail") name = "/author_site_nomail.html";
  if (name === "/contact") name = "/contact_page.html";
  const file = url.startsWith("/ext/")
    ? path.join(EXT, url.slice(5))
    : path.join(PAGES, name);
  if (!file.startsWith(EXT) && !file.startsWith(PAGES)) { res.writeHead(403).end(); return; }
  fs.readFile(file, (error, body) => {
    if (error) { res.writeHead(404).end("not found"); return; }
    const type = MIME[path.extname(file)] || "text/plain";
    // Fixtures cannot know the port ahead of time, so they write __BASE__.
    const payload = type === "text/html"
      ? Buffer.from(body.toString("utf8").split("__BASE__").join(BASE))
      : body;
    res.writeHead(200, { "Content-Type": type }).end(payload);
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
BASE = `http://127.0.0.1:${server.address().port}`;

let failures = 0;
function check(label, condition, detail = "") {
  if (!condition) failures += 1;
  console.log(`  ${condition ? "PASS" : "FAIL"}  ${label}${detail ? `  → ${detail}` : ""}`);
}

async function extractOn(page, file) {
  await page.goto(`${BASE}/${file}`, { waitUntil: "domcontentloaded" });
  await page.addScriptTag({
    type: "module",
    content: `
      import { extractPage } from "${BASE}/ext/src/content/extract.js";
      const registry = await (await fetch("${BASE}/ext/src/shared/registry.json")).json();
      window.__records = extractPage(document, location.href, registry);
      window.__ready = true;`,
  });
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 10000 });
  return page.evaluate(() => window.__records);
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "orynx-prof-"));
const context = await chromium.launchPersistentContext(profile, {
  headless: true,
  executablePath: CHROME,
  args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`, "--no-sandbox"],
});
const page = await context.newPage();

console.log("\nJSON-LD book page");
{
  const records = await extractOn(page, "jsonld_book.html");
  check("one book extracted", records.length === 1, `got ${records.length}`);
  const r = records[0] || {};
  check("title", r.bookName === "The Quiet Harbour", r.bookName);
  check("author", r.author === "Amara Nwosu", r.author);
  check("isbn normalised", r.isbn === "9781234567897", r.isbn);
  check("publish date", r.publishDate === "2026-03-15", r.publishDate);
  check("publisher", r.publisher === "Demo Hybrid Press", r.publisher);
  check("reviews count", r.reviewsCount === 4, String(r.reviewsCount));
  check("ratings count", r.ratingsCount === 7, String(r.ratingsCount));
  check("average rating", r.averageRating === 4.1, String(r.averageRating));
  check("price", (r.price || "").startsWith("14.99"), r.price);
  check("format", r.format === "Paperback", r.format);
  check("page count", r.pageCount === 312, String(r.pageCount));
  check("email via canonical domain", r.email === "hello@demopress.example", r.email);
  check("phone", (r.phone || "").includes("212"), r.phone);
  check("whatsapp from wa.me", r.whatsapp === "+2348012345678", r.whatsapp);
  check("linkedin", (r.linkedin || "").includes("demo-hybrid-press"), r.linkedin);
  check("instagram", (r.instagram || "").includes("demopress"), r.instagram);
  check("contact page discovered", (r.contactPage || "").includes("submissions"), r.contactPage);
  check("classified hybrid from page text", r.category === "publisher_hybrid", r.category);
  check("pitch generated", (r.idealPitch || "").length > 20, (r.idealPitch || "").slice(0, 44));
  check("priority scored", typeof r.priority === "number" && r.priority > 0, String(r.priority));
  check("via jsonld", r.extractedBy === "jsonld", r.extractedBy);
}

console.log("\nUnstructured listing page (heuristics only)");
{
  const records = await extractOn(page, "listing.html");
  check("two real books, news card rejected", records.length === 2,
        records.map((r) => r.bookName).join(" | "));
  const tide = records.find((r) => r.bookName === "Tidewater") || {};
  check("multi-author byline split", tide.author === "Amara Nwosu; Peter Blake", tide.author);
  check("isbn from body text", tide.isbn === "9780306406157", tide.isbn);
  check("review count not fused with price", tide.reviewsCount === 1204, String(tide.reviewsCount));
  check("publish date from text", (tide.publishDate || "").includes("2019"), tide.publishDate);
  check("price captured", (tide.price || "").includes("12.99"), tide.price);
  const ash = records.find((r) => r.bookName === "Ashfall") || {};
  check("second book author", ash.author === "Chidi Okonkwo", ash.author);
  check("whatsapp from labelled text", (ash.whatsapp || "").includes("7700"), ash.whatsapp);
  check("third-party generic inbox dropped", !ash.email, String(ash.email));
}

console.log("\nByline parsing across name formats");
{
  const records = await extractOn(page, "bylines.html");
  const byTitle = Object.fromEntries(records.map((r) => [r.bookName, r.author]));
  check("acronym does not become an author", byTitle["Tidewater"] === "Amara Nwosu; Peter Blake", byTitle["Tidewater"]);
  check("field label does not become an author", byTitle["Ashfall"] === "Chidi Okonkwo", byTitle["Ashfall"]);
  check("initials preserved", byTitle["Three Voices"] === "A. Ali; B. Bell; C. Cruz", byTitle["Three Voices"]);
  check("lowercase particle kept", byTitle["River Between"] === "Ngugi wa Thiongo", byTitle["River Between"]);
  check("internal capital kept", byTitle["Late Modernism"] === "Ronan McDonald", byTitle["Late Modernism"]);
  check("apostrophe kept, price excluded", byTitle["At Swim"] === "Flann O'Brien", byTitle["At Swim"]);
  check("dutch particle kept", byTitle["Ninth Symphony"] === "Ludwig van Beethoven", byTitle["Ninth Symphony"]);
  check("non-book card rejected", !("Company News" in byTitle), Object.keys(byTitle).join(","));
}

console.log("\nPublisher shop listing (the Pegasus case: cover + title + bare author)");
{
  const records = await extractOn(page, "shop_listing.html");
  const byTitle = Object.fromEntries(records.map((r) => [r.bookName, r.author]));
  check("finds books with no ISBN, no price and no 'by'", records.length >= 5,
        `${records.length}: ${Object.keys(byTitle).join(" | ")}`);
  check("author from a class-named element", byTitle["Ghost"] === "Lea Tonin", byTitle["Ghost"]);
  check("internal capital in surname", byTitle["Out of the Main(e)"] === "Cindy McCarley",
        byTitle["Out of the Main(e)"]);
  check("credential suffix stripped", (byTitle["Essence Merging"] || "").startsWith("Colleen Quinn"),
        byTitle["Essence Merging"]);
  check("structural card with no useful classes", byTitle["Red Umbrellas"] === "Marta Oyelaran",
        byTitle["Red Umbrellas"]);
  check("particle name in a structural card", byTitle["Quiet Tide"] === "Ngugi wa Thiongo",
        byTitle["Quiet Tide"]);
  check("navigation links are not books", !("About Us" in byTitle) && !("Fiction" in byTitle),
        Object.keys(byTitle).join(","));
  const ghost = records.find((r) => r.bookName === "Ghost") || {};
  check("known platform recognised", ghost.category === "publisher_vanity", ghost.category);
  check("own-domain email captured", ghost.email === "enquiries@pegasuspublishers.com", ghost.email);
  check("cover url captured", Boolean(ghost.coverUrl), ghost.coverUrl);
}

console.log("\nCover-only grid (the Goodreads case: title lives in the image alt)");
{
  const records = await extractOn(page, "cover_grid.html");
  const titles = records.map((r) => r.bookName);
  check("every cover tile becomes its own book", records.length === 6,
        `${records.length}: ${titles.join(" | ")}`);
  check("title read from alt text", titles.includes("Hollow Bones"), titles.join(" | "));
  check("parenthetical kept", titles.includes("The Pirate Queen (a novel)"), titles.join(" | "));
  check("'book cover' suffix stripped", titles.includes("American Hagwon"), titles.join(" | "));
  check("visible title beats alt text", titles.includes("Grim Tidings"), titles.join(" | "));
  const grim = records.find((r) => r.bookName === "Grim Tidings") || {};
  check("author still read where present", grim.author === "B. K. Borison", grim.author);
  check("decorative images rejected", !titles.some((t) => /image|logo/i.test(t)),
        titles.join(" | "));
}

console.log("\nGenre rows must not become books");
{
  const records = await extractOn(page, "genre_rows.html");
  const titles = records.map((r) => r.bookName);
  check("genre labels are not books",
        !titles.some((t) => /^(fiction|historical fiction)$/i.test(t)), titles.join(" | "));
  check("the covers inside each row are", records.length === 5,
        `${records.length}: ${titles.join(" | ")}`);
  check("titles come from the covers", titles.includes("Hollow Bones"), titles.join(" | "));
}

console.log("\nUnknown site must not get the relaxed threshold");
{
  const records = await extractOn(page, "generic_page.html");
  check("no books invented on an unrelated page", records.length === 0,
        records.map((r) => r.bookName).join(" | "));
}

console.log("\nMeta-tag-only page");
{
  const records = await extractOn(page, "meta_book.html");
  check("one book from meta tags", records.length === 1, `got ${records.length}`);
  const r = records[0] || {};
  check("title", r.bookName === "The Salt Road", r.bookName);
  check("author", r.author === "Marion Adebayo", r.author);
  check("isbn", r.isbn === "9780306406157", r.isbn);
  check("launch date", r.launchDate === "2025-11-04", r.launchDate);
  check("classified vanity from page text", r.category === "publisher_vanity", r.category);
  check("own-domain email kept", r.email === "editor@oldpublisher.example", r.email);
  check("vanity signal lifts priority", r.priority > 40, String(r.priority));
  check("via meta", r.extractedBy === "meta", r.extractedBy);
}

console.log("\nLate-rendering grid (content arrives after document_idle)");
{
  await page.goto(`${BASE}/lazy_listing.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#orynx-badge", { timeout: 15000 });
  const initial = await page.textContent("#orynx-count");
  // The observer is debounced, so allow it a moment to notice the new grid.
  await page.waitForFunction(
    () => document.querySelector("#orynx-count")?.textContent === "1",
    null, { timeout: 12000 },
  ).catch(() => {});
  const after = await page.textContent("#orynx-count");
  check("finds nothing at first load", initial === "0", initial);
  check("rescans once the grid renders", after === "1", after);
}

console.log("\nDiagnostics");
{
  await page.goto(`${BASE}/generic_page.html`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#orynx-badge", { timeout: 15000 });
  await page.click("#orynx-badge");
  await page.click("#orynx-why");
  const report = await page.textContent(".orynx-diag");
  check("diagnostics render", Boolean(report && report.includes("\"found\": 0")));
  check("reports platform context", report.includes("bookContext"));
  check("explains why cards were rejected", report.includes("rejectedSamples"));
}

console.log("\nPanel UI (extension loaded)");
{
  await page.goto(`${BASE}/jsonld_book.html`, { waitUntil: "domcontentloaded" });
  const appeared = await page.waitForSelector("#orynx-badge", { timeout: 15000 })
    .then(() => true).catch(() => false);
  check("content script injected the badge", appeared);
  if (appeared) {
    check("badge shows one record", (await page.textContent("#orynx-count")) === "1");
    await page.click("#orynx-badge");
    check("panel opens", await page.isVisible("#orynx-panel"));
    check("book is listed", (await page.textContent(".orynx-list")).includes("The Quiet Harbour"));
    await page.click('.orynx-chip[data-channel="whatsapp"]');
    check("whatsapp filter keeps a record that has one", (await page.textContent("#orynx-count")) === "1");

    await page.goto(`${BASE}/listing.html`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#orynx-badge", { timeout: 15000 });
    await page.click("#orynx-badge");
    await page.click('.orynx-chip[data-channel="email"]');
    check("email filter hides rows with no email", (await page.textContent("#orynx-count")) === "0");
  }
}

console.log("\nBook detail page (deep extraction)");
{
  const records = await extractOn(page, "detail_book.html");
  check("one book on a detail page", records.length === 1, `got ${records.length}`);
  const r = records[0] || {};
  check("title", r.bookName === "Hollow Bones", r.bookName);
  check("marked as a detail page", r.isDetailPage === true, String(r.isDetailPage));
  check("original title from a labelled field", r.originalTitle === "Ndụ na Ụlọ", r.originalTitle);
  check("edition", (r.edition || "").includes("First edition"), r.edition);
  check("series", (r.series || "").includes("Harbour Cycle"), r.series);
  check("awards", (r.awards || "").includes("Caine"), r.awards);
  check("expected publication", (r.expectedPublication || "").includes("2026"), r.expectedPublication);
  check("genres", (r.genres || "").includes("Literary Fiction"), r.genres);
  check("pages", r.pageCount === 312, String(r.pageCount));
  check("ratings count", r.ratingsCount === 1284, String(r.ratingsCount));
  check("reviews count", r.reviewsCount === 213, String(r.reviewsCount));
  check("views count", r.viewsCount === 4902, String(r.viewsCount));
  check("want-to-read count", r.wantToReadCount === 8110, String(r.wantToReadCount));
  check("currently reading", r.currentlyReadingCount === 512, String(r.currentlyReadingCount));
  check("editions count", r.editionsCount === 7, String(r.editionsCount));
  check("more editions link", (r.moreEditionsUrl || "").includes("editions"), r.moreEditionsUrl);
  check("author bio", (r.authorBio || "").includes("Nigerian novelist"), (r.authorBio || "").slice(0, 40));
  check("author website, not a social host", r.authorWebsite === "https://amaranwosu.example", r.authorWebsite);
  check("author email", r.email === "hello@amaranwosu.example", r.email);
  check("instagram", (r.instagram || "").includes("amara.writes"), r.instagram);
  check("tiktok", (r.tiktok || "").includes("amarawrites"), r.tiktok);
  check("linkedin", (r.linkedin || "").includes("amara-nwosu"), r.linkedin);
  check("review text captured", (r.topReviews || "").includes("devastating"), (r.topReviews || "").slice(0, 40));
  check("review rating kept", (r.topReviews || "").includes("[5/5]"), (r.topReviews || "").slice(0, 20));
  check("AI columns present and empty", r.found_email === "" && r.outreach_message === "");
}

console.log("\nLink collection for the queue");
{
  await page.goto(`${BASE}/crawl_listing.html`, { waitUntil: "domcontentloaded" });
  await page.addScriptTag({
    type: "module",
    content: `
      import { collectBookLinks } from "${BASE}/ext/src/content/extract.js";
      window.__links = collectBookLinks(document, location.href);
      window.__ready2 = true;`,
  });
  await page.waitForFunction(() => window.__ready2 === true, null, { timeout: 10000 });
  const links = await page.evaluate(() => window.__links);
  check("collects the book links", links.length === 2, JSON.stringify(links.map((l) => l.url)));
  check("off-site links excluded",
        !links.some((l) => /amazon|goodreads/.test(l.url)), JSON.stringify(links));
  check("titles carried with the links",
        links.some((l) => l.title === "Hollow Bones"), JSON.stringify(links.map((l) => l.title)));
}

console.log("\nPopup");
{
  // The extension id comes from the service worker registered on load.
  const worker = context.serviceWorkers()[0]
    || await context.waitForEvent("serviceworker", { timeout: 10000 }).catch(() => null);
  const extensionId = worker ? new URL(worker.url()).host : null;
  check("service worker registered", Boolean(extensionId), extensionId || "none");

  if (extensionId) {
    const popup = await context.newPage();
    const errors = [];
    popup.on("pageerror", (error) => errors.push(error.message));
    await popup.goto(`chrome-extension://${extensionId}/src/popup/popup.html`);
    await popup.waitForSelector(".tab", { timeout: 8000 });
    check("popup loads without script errors", errors.length === 0, errors.join("; "));
    check("defaults to the This page view",
          await popup.getAttribute('.tab[data-view="page"]', "class") === "tab on");
    check("has a Saved view", await popup.isVisible('.tab[data-view="saved"]'));
    check("Save button present on the page view", await popup.isVisible("#save"));
    check("summary rendered",
          !(await popup.textContent("#summary")).includes("Loading"),
          await popup.textContent("#summary"));
    await popup.click("#rescan");
    check("rescan reports a count rather than doing nothing",
          /Found \d+/.test(await popup.textContent("#rescan")),
          await popup.textContent("#rescan"));
    await popup.close();
  }
}

console.log("\nQueue: visit each book link and save it");
{
  const worker = context.serviceWorkers()[0];
  const extensionId = worker ? new URL(worker.url()).host : null;
  if (!extensionId) {
    check("service worker available for the queue run", false);
  } else {
    const driver = await context.newPage();
    await driver.goto(`chrome-extension://${extensionId}/src/popup/popup.html`);

    // Clear the library so counts below are unambiguous.
    await driver.evaluate(() => chrome.runtime.sendMessage({ type: "orynx:clear" }));

    const links = [
      { url: `${BASE}/book/hollow-bones`, title: "Hollow Bones" },
      { url: `${BASE}/book/tidewater`, title: "Tidewater" },
    ];
    const started = await driver.evaluate(
      ([queue]) => chrome.runtime.sendMessage({
        type: "orynx:queue:start",
        links: queue,
        // Short waits: the fixtures are local and render immediately.
        // Chaining is exercised separately; this checks the book stage alone.
        options: { delayMs: 200, settleMs: 300, chainAuthors: false },
      }),
      [links],
    );
    check("queue accepted the run", started?.ok === true && started.total === 2,
          JSON.stringify(started));

    // Polled from Node rather than with waitForFunction: this page is backgrounded
    // while the queue opens its tabs, and requestAnimationFrame — which
    // waitForFunction polls on — does not fire in a background tab.
    let finished = null;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const status = await driver.evaluate(() =>
        chrome.runtime.sendMessage({ type: "orynx:queue:status" }));
      if (status?.state && !status.state.running && status.state.done > 0) {
        finished = status.state;
        break;
      }
    }

    check("queue completed both pages", finished?.done === 2, JSON.stringify(finished));
    check("nothing failed", finished?.failed === 0, JSON.stringify(finished?.errors || []));

    const saved = await driver.evaluate(async () => {
      const response = await chrome.runtime.sendMessage({ type: "orynx:list" });
      return response.records;
    });
    check("both books saved to the library", saved.length === 2,
          saved.map((r) => r.bookName).join(" | "));

    const hollow = saved.find((r) => r.bookName === "Hollow Bones") || {};
    check("detail data saved, not just the title", hollow.originalTitle === "Ndụ na Ụlọ",
          hollow.originalTitle);
    check("author email saved", hollow.email === "hello@amaranwosu.example", hollow.email);
    check("socials saved", Boolean(hollow.tiktok && hollow.linkedin), `${hollow.tiktok} / ${hollow.linkedin}`);
    check("review content saved", (hollow.topReviews || "").length > 40);
    check("background tabs were closed", (await context.pages()).length <= 3,
          String((await context.pages()).length));

    await driver.close();
  }
}

console.log("\nAuthor page extraction");
{
  await page.goto(`${BASE}/author/amara-nwosu`, { waitUntil: "domcontentloaded" });
  await page.addScriptTag({
    type: "module",
    content: `
      import { extractAuthor } from "${BASE}/ext/src/content/extract.js";
      window.__profile = extractAuthor(document, location.href);
      window.__ready3 = true;`,
  });
  await page.waitForFunction(() => window.__ready3 === true, null, { timeout: 10000 });
  const profile = await page.evaluate(() => window.__profile);

  check("recognised as an author page", Boolean(profile), JSON.stringify(profile));
  check("name without the trailing parenthetical", profile?.authorName === "Amara Nwosu",
        profile?.authorName);
  check("bio", (profile?.authorBio || "").includes("Nigerian novelist"),
        (profile?.authorBio || "").slice(0, 40));
  check("own website from the labelled field", profile?.authorWebsite === "https://amaranwosu.example",
        profile?.authorWebsite);
  check("born", profile?.authorBorn === "Enugu, Nigeria", profile?.authorBorn);
  check("genres", (profile?.authorGenres || "").includes("Literary Fiction"), profile?.authorGenres);
  check("socials", Boolean(profile?.authorSocials?.tiktok && profile?.authorSocials?.linkedin),
        JSON.stringify(profile?.authorSocials));
  check("book count", profile?.authorBookCount === 3, String(profile?.authorBookCount));
}

console.log("\nAuthor hop: enrich saved books from the author page");
{
  const worker = context.serviceWorkers()[0];
  const extensionId = worker ? new URL(worker.url()).host : null;
  if (!extensionId) {
    check("service worker available for the author run", false);
  } else {
    const driver = await context.newPage();
    await driver.goto(`chrome-extension://${extensionId}/src/popup/popup.html`);
    await driver.evaluate(() => chrome.runtime.sendMessage({ type: "orynx:clear" }));

    // A saved book with an author link but nothing about the author.
    await driver.evaluate((record) =>
      chrome.runtime.sendMessage({ type: "orynx:save", records: [record] }),
      {
        bookName: "Hollow Bones", author: "Amara Nwosu", isbn: "9781234567897",
        authorUrl: `${BASE}/author/amara-nwosu`, email: "", authorBio: null,
        authorWebsite: null, tiktok: null, linkedin: null,
      },
    );

    await driver.evaluate((queue) => chrome.runtime.sendMessage({
      type: "orynx:queue:start", mode: "authors", links: queue,
      options: { delayMs: 200, settleMs: 300, followAuthorWebsite: false },
    }), [{ url: `${BASE}/author/amara-nwosu`, title: "Amara Nwosu", author: "Amara Nwosu" }]);

    let finished = null;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const status = await driver.evaluate(() =>
        chrome.runtime.sendMessage({ type: "orynx:queue:status" }));
      if (status?.state && !status.state.running && status.state.done > 0) {
        finished = status.state;
        break;
      }
    }
    check("author run completed", finished?.done === 1, JSON.stringify(finished));
    check("a record was updated", finished?.saved === 1, JSON.stringify(finished));

    const saved = await driver.evaluate(async () => {
      const response = await chrome.runtime.sendMessage({ type: "orynx:list" });
      return response.records;
    });
    check("still one book, not a duplicate", saved.length === 1,
          saved.map((r) => r.bookName).join(" | "));
    const book = saved[0] || {};
    check("bio written onto the book", (book.authorBio || "").includes("Nigerian novelist"),
          (book.authorBio || "").slice(0, 40));
    check("author website written on", book.authorWebsite === "https://amaranwosu.example",
          book.authorWebsite);
    check("socials written on", Boolean(book.tiktok && book.linkedin),
          `${book.tiktok} / ${book.linkedin}`);
    check("author page recorded for provenance",
          (book.authorPageUrl || "").includes("/author/"), book.authorPageUrl);

    await driver.close();
  }
}

console.log("\nSecond hop: follow the author's own site for an address");
{
  const worker = context.serviceWorkers()[0];
  const extensionId = worker ? new URL(worker.url()).host : null;
  if (!extensionId) {
    check("service worker available for the second hop", false);
  } else {
    const driver = await context.newPage();
    await driver.goto(`chrome-extension://${extensionId}/src/popup/popup.html`);
    await driver.evaluate(() => chrome.runtime.sendMessage({ type: "orynx:clear" }));

    await driver.evaluate((record) =>
      chrome.runtime.sendMessage({ type: "orynx:save", records: [record] }),
      {
        bookName: "Red Umbrellas", author: "Marta Oyelaran", isbn: "9789998887776",
        authorUrl: `${BASE}/author/marta-oyelaran`, email: "", authorWebsite: null,
      },
    );

    await driver.evaluate((queue) => chrome.runtime.sendMessage({
      type: "orynx:queue:start", mode: "authors", links: queue,
      options: { delayMs: 200, settleMs: 300, followAuthorWebsite: true },
    }), [{ url: `${BASE}/author/marta-oyelaran`, title: "Marta Oyelaran", author: "Marta Oyelaran" }]);

    let done = false;
    for (let attempt = 0; attempt < 30 && !done; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const status = await driver.evaluate(() =>
        chrome.runtime.sendMessage({ type: "orynx:queue:status" }));
      done = Boolean(status?.state && !status.state.running && status.state.done > 0);
    }
    check("second hop finished", done);

    const saved = await driver.evaluate(async () => {
      const response = await chrome.runtime.sendMessage({ type: "orynx:list" });
      return response.records;
    });
    const book = saved[0] || {};
    check("author site recorded", (book.authorWebsite || "").includes("/site"), book.authorWebsite);
    check("email found on the author's own site",
          book.email === "hello@amaranwosu.example", book.email);
    check("third-party inbox not taken", book.email !== "info@bigpublisher.com", book.email);

    await driver.close();
  }
}

console.log("\nThird hop: the contact page, when the homepage has no address");
{
  const worker = context.serviceWorkers()[0];
  const extensionId = worker ? new URL(worker.url()).host : null;
  const driver = await context.newPage();
  await driver.goto(`chrome-extension://${extensionId}/src/popup/popup.html`);
  await driver.evaluate(() => chrome.runtime.sendMessage({ type: "orynx:clear" }));

  await driver.evaluate((record) =>
    chrome.runtime.sendMessage({ type: "orynx:save", records: [record] }),
    {
      bookName: "Quiet Tide", author: "Marta Oyelaran", isbn: "9789998887000",
      authorUrl: `${BASE}/author/marta-nomail`, email: "", authorWebsite: null,
    },
  );

  await driver.evaluate((queue) => chrome.runtime.sendMessage({
    type: "orynx:queue:start", mode: "authors", links: queue,
    options: { delayMs: 150, settleMs: 250, chainAuthors: false },
  }), [{ url: `${BASE}/author/marta-nomail`, title: "Marta Oyelaran", author: "Marta Oyelaran" }]);

  let done = false;
  for (let attempt = 0; attempt < 40 && !done; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const status = await driver.evaluate(() =>
      chrome.runtime.sendMessage({ type: "orynx:queue:status" }));
    done = Boolean(status?.state && !status.state.running && status.state.done > 0);
  }
  check("run finished", done);

  const book = (await driver.evaluate(async () => {
    const response = await chrome.runtime.sendMessage({ type: "orynx:list" });
    return response.records;
  }))[0] || {};
  check("email came from the contact page", book.email === "marta@martaoyelaran.example", book.email);
  check("phone came from the contact page", (book.authorPhone || "").includes("801"), book.authorPhone);
  check("social found on the way", (book.facebook || "").includes("martaoyelaran"), book.facebook);
  await driver.close();
}

console.log("\nChaining: books then authors in one run");
{
  const worker = context.serviceWorkers()[0];
  const extensionId = worker ? new URL(worker.url()).host : null;
  const driver = await context.newPage();
  await driver.goto(`chrome-extension://${extensionId}/src/popup/popup.html`);
  await driver.evaluate(() => chrome.runtime.sendMessage({ type: "orynx:clear" }));

  await driver.evaluate((record) =>
    chrome.runtime.sendMessage({ type: "orynx:save", records: [record] }),
    {
      bookName: "Seed", author: "Marta Oyelaran", isbn: "9789998880000",
      authorUrl: `${BASE}/author/marta-oyelaran`, email: "",
    },
  );

  await driver.evaluate((queue) => chrome.runtime.sendMessage({
    type: "orynx:queue:start", links: queue,
    options: { delayMs: 150, settleMs: 250, chainAuthors: true },
  }), [{ url: `${BASE}/book/tidewater`, title: "Tidewater" }]);

  let state = null;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const status = await driver.evaluate(() =>
      chrome.runtime.sendMessage({ type: "orynx:queue:status" }));
    if (status?.state && !status.state.running && status.state.done > 0) {
      state = status.state;
      break;
    }
  }
  check("run finished", Boolean(state), JSON.stringify(state));
  check("the author stage ran without a second click", state?.mode === "authors",
        JSON.stringify(state));

  const saved = await driver.evaluate(async () =>
    (await chrome.runtime.sendMessage({ type: "orynx:list" })).records);
  const seed = saved.find((r) => r.bookName === "Seed") || {};
  check("the seeded book was enriched by the chained stage",
        (seed.authorBio || "").includes("historical fiction"), (seed.authorBio || "").slice(0, 40));
  await driver.close();
}

console.log("\nCSV shape");
{
  const worker = context.serviceWorkers()[0];
  const extensionId = worker ? new URL(worker.url()).host : null;
  const driver = await context.newPage();
  await driver.goto(`chrome-extension://${extensionId}/src/popup/popup.html`);
  const csv = await driver.evaluate(async () => {
    const { toCsv, HEADERS } = await import(chrome.runtime.getURL("src/content/extract.js"));
    const rows = (await chrome.runtime.sendMessage({ type: "orynx:list" })).records;
    return { text: toCsv(rows), headers: HEADERS };
  });
  const header = csv.text.split("\n")[0].replace(/^\ufeff/, "");
  check("headers are the requested fields", header.startsWith("Book title,Description,Rating,Reviews,Voters,Views"), header.slice(0, 60));
  check("no leftover scoring columns", !/priority|tier|services|idealPitch/i.test(header), header);
  check("AI columns last", header.endsWith("found_email,outreach_message"), header.slice(-40));
  check("column count", csv.headers.length === 31, String(csv.headers.length));
  await driver.close();
}

await context.close();
fs.rmSync(profile, { recursive: true, force: true });
server.close();
console.log(failures ? `\n${failures} check(s) FAILED\n` : "\nAll checks passed\n");
process.exit(failures ? 1 : 0);
