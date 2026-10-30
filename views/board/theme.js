// Classic (non-module) script loaded in <head> so the theme is set before first paint.
// Inside AstrBot the server already writes data-theme on <html> (from the ?theme= query the dashboard adds)
// and the bridge keeps it in sync. Standalone (demo, public page) we use ?theme=dark|light, then the OS setting.
(() => {
  const root = document.documentElement;
  const fromQuery = new URLSearchParams(location.search).get("theme");
  if (fromQuery === "dark" || fromQuery === "light") {
    root.setAttribute("data-theme", fromQuery);
  } else if (!root.hasAttribute("data-theme")) {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => root.setAttribute("data-theme", query.matches ? "dark" : "light");
    apply();
    query.addEventListener("change", apply);
  }
})();
