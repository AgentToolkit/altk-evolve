// Light / dark theme shared by every page. The choice is remembered in this browser; dark is the default.
// Loaded in <head> so the saved theme applies before the first paint.
(function () {
  const KEY = "trajectory-theme";
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const save = (v) => { try { localStorage.setItem(KEY, v); } catch { /* private mode: the choice lasts for this page only */ } };
  const apply = (theme) => {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = theme;
    document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
      b.setAttribute("aria-pressed", String(theme === "light"));
      b.textContent = theme === "light" ? "☾ Dark" : "☀ Light";
      b.title = `Switch to the ${theme === "light" ? "dark" : "light"} theme`;
    });
  };
  apply(read() === "light" ? "light" : "dark");
  document.addEventListener("DOMContentLoaded", () => {
    apply(document.documentElement.dataset.theme);
    document.addEventListener("click", (e) => {
      if (!e.target.closest("[data-theme-toggle]")) return;
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      save(next);
      apply(next);
    });
  });
  // Another tab changed the theme.
  window.addEventListener("storage", (e) => { if (e.key === KEY) apply(e.newValue === "light" ? "light" : "dark"); });
})();
