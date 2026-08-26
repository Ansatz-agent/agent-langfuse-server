(() => {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("ansatz-analytics-theme");
  if (savedTheme === "dark" || savedTheme === "light") root.dataset.theme = savedTheme;
  document.querySelector("[data-theme-toggle]")?.addEventListener("click", () => {
    const theme = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = theme;
    localStorage.setItem("ansatz-analytics-theme", theme);
  });
  const rail = document.querySelector("[data-filter-rail]");
  const toggle = document.querySelector("[data-filter-toggle]");
  toggle?.addEventListener("click", () => {
    const collapsed = rail?.classList.toggle("is-collapsed") ?? false;
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.textContent = collapsed ? "›" : "‹";
  });
})();
