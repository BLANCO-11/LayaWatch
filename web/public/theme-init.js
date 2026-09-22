/* LayaWatch pre-paint theme resolver (decision D-012).
 * Blocking external script: sets <html data-theme> before first paint from the
 * stored preference, else prefers-color-scheme, else dark. External (not inline)
 * so the export keeps the no-inline-scripts CSP. Stored values "dark" and
 * "light" are explicit; any other stored value (including "system", written by
 * the Settings theme select) follows prefers-color-scheme live. */
(function () {
  var mq = window.matchMedia("(prefers-color-scheme: light)");
  function isExplicit(value) {
    return value === "light" || value === "dark";
  }
  function stored() {
    try {
      return localStorage.getItem("laya-theme");
    } catch (err) {
      return null;
    }
  }
  function apply() {
    var value = stored();
    document.documentElement.dataset.theme = isExplicit(value)
      ? value
      : mq.matches
        ? "light"
        : "dark";
  }
  apply();
  var follow = function () {
    if (!isExplicit(stored())) apply();
  };
  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", follow);
  } else if (typeof mq.addListener === "function") {
    mq.addListener(follow);
  }
})();
