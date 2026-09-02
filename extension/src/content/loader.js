/**
 * Content script entry point.
 *
 * MV3 content scripts cannot use static imports, so the real code is loaded as
 * a module from an extension URL. This keeps the source modular instead of one
 * bundled file, and needs no build step.
 */
(async () => {
  if (window.__orynxLoaded) return;
  window.__orynxLoaded = true;
  try {
    const module = await import(chrome.runtime.getURL("src/content/panel.js"));
    await module.init();
  } catch (error) {
    console.warn("[Orynx] failed to start:", error);
  }
})();
