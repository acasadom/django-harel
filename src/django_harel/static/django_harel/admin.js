// Draws the execution's machine (the <pre class="harel-diagram"> blocks) with Mermaid, in the
// admin's light or dark theme.
document.addEventListener("DOMContentLoaded", function () {
  if (!window.mermaid) {
    return; // Mermaid didn't load (no network?): the diagram's source stays readable as text
  }
  var root = document.documentElement;
  var dark =
    root.dataset.theme === "dark" ||
    (root.dataset.theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  window.mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default" });
  window.mermaid.run({ querySelector: ".harel-diagram" });
});
