/* LayaWatch pre-paint theme resolver (decision D-012).
 * Blocking external script: sets <html data-theme> before first paint from the
 * stored preference, else prefers-color-scheme, else dark. External (not inline)
 * so the export keeps the no-inline-scripts CSP. */
(function () {
  try {
    var stored = localStorage.getItem("laya-theme");
    var theme =
      stored === "light" || stored === "dark"
        ? stored
        : window.matchMedia("(prefers-color-scheme: light)").matches
          ? "light"
          : "dark";
    document.documentElement.dataset.theme = theme;
  } catch (err) {
    document.documentElement.dataset.theme = "dark";
  }
})();
