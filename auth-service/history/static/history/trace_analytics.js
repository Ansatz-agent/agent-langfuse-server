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

  document.querySelectorAll(".trend-value[data-bar-height]").forEach((value) => {
    const barHeight = Number.parseFloat(value.dataset.barHeight || "0");
    if (Number.isFinite(barHeight)) value.style.bottom = `calc(${barHeight}% + 5px)`;
  });

  const viewButtons = [...document.querySelectorAll("[data-trace-view-target]")];
  const views = [...document.querySelectorAll("[data-trace-view]")];
  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.traceViewTarget;
      viewButtons.forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-selected", String(active));
      });
      views.forEach((view) => {
        const active = view.dataset.traceView === target;
        view.classList.toggle("is-active", active);
        view.hidden = !active;
      });
    });
  });

  const executionRows = [...document.querySelectorAll(".trace-execution-row")];
  const filterButtons = [...document.querySelectorAll("[data-trace-filter]")];
  const traceSearch = document.querySelector("[data-trace-search]");
  let activeFilter = "all";
  const applyTraceFilters = () => {
    const query = (traceSearch?.value || "").trim().toLowerCase();
    executionRows.forEach((row) => {
      const kind = row.dataset.observationKind;
      const matchesKind = activeFilter === "all"
        || kind === activeFilter
        || (activeFilter === "error" && row.classList.contains("is-error"));
      const matchesQuery = !query || row.textContent.toLowerCase().includes(query);
      row.hidden = !(matchesKind && matchesQuery);
    });
  };
  filterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeFilter = button.dataset.traceFilter || "all";
      filterButtons.forEach((candidate) => candidate.classList.toggle("is-active", candidate === button));
      applyTraceFilters();
    });
  });
  traceSearch?.addEventListener("input", applyTraceFilters);

  const inspector = document.querySelector("[data-trace-inspector]");
  const stepPanelHost = document.querySelector("[data-trace-step-panel-host]");
  const stepFragmentCache = new Map();
  let stepRequestController = null;

  const activateStepLink = (stepId) => {
    document.querySelectorAll("[data-trace-step-link]").forEach((candidate) => {
      const active = candidate.dataset.stepId === stepId;
      candidate.classList.toggle("is-active", active);
      if (active) {
        const stepScroller = candidate.closest(".trace-step-scroll");
        stepScroller?.scrollTo({
          left: candidate.offsetLeft - ((stepScroller.clientWidth - candidate.offsetWidth) / 2),
          top: candidate.offsetTop - ((stepScroller.clientHeight - candidate.offsetHeight) / 2),
        });
      }
    });
  };

  const initialStepLink = document.querySelector("[data-trace-step-link].is-active");
  if (initialStepLink) activateStepLink(initialStepLink.dataset.stepId);
  let stepResizeFrame = null;
  const centerActiveStep = () => {
    if (stepResizeFrame) window.cancelAnimationFrame(stepResizeFrame);
    stepResizeFrame = window.requestAnimationFrame(() => {
      const activeStepLink = document.querySelector("[data-trace-step-link].is-active");
      if (activeStepLink) activateStepLink(activeStepLink.dataset.stepId);
    });
  };
  window.addEventListener("resize", centerActiveStep);

  const loadTraceStep = async (link, { pushHistory = true } = {}) => {
    if (!stepPanelHost) return;
    const stepId = link.dataset.stepId;
    const fragmentUrl = link.dataset.fragmentUrl;
    if (!stepId || !fragmentUrl) return;
    stepRequestController?.abort();
    stepRequestController = new AbortController();
    stepPanelHost.setAttribute("aria-busy", "true");
    try {
      let html = stepFragmentCache.get(stepId);
      if (!html) {
        const response = await fetch(fragmentUrl, {
          headers: { "X-Requested-With": "TraceInspector" },
          signal: stepRequestController.signal,
        });
        if (!response.ok) throw new Error(`step fragment ${response.status}`);
        html = await response.text();
        stepFragmentCache.set(stepId, html);
      }
      stepPanelHost.innerHTML = html;
      activateStepLink(stepId);
      if (pushHistory) history.pushState({ traceStep: stepId }, "", link.href);
    } catch (error) {
      if (error.name !== "AbortError") window.location.assign(link.href);
    } finally {
      stepPanelHost.removeAttribute("aria-busy");
    }
  };

  inspector?.addEventListener("click", (event) => {
    const link = event.target.closest("[data-trace-step-link]");
    if (!link) return;
    event.preventDefault();
    loadTraceStep(link);
  });

  window.addEventListener("popstate", () => {
    const stepId = new URL(window.location.href).searchParams.get("step") || "overview";
    const link = [...document.querySelectorAll("[data-trace-step-link]")]
      .find((candidate) => candidate.dataset.stepId === stepId);
    if (link) loadTraceStep(link, { pushHistory: false });
  });

  document.addEventListener("click", (event) => {
    const detailButton = event.target.closest("[data-step-detail-target]");
    if (detailButton) {
      const panel = detailButton.closest("[data-trace-step-panel]");
      const target = detailButton.dataset.stepDetailTarget;
      panel.querySelectorAll("[data-step-detail-target]").forEach((button) => {
        button.classList.toggle("is-active", button === detailButton);
      });
      panel.querySelectorAll("[data-step-detail]").forEach((view) => {
        const active = view.dataset.stepDetail === target;
        view.classList.toggle("is-active", active);
        view.hidden = !active;
      });
      return;
    }

    const wrapButton = event.target.closest("[data-trace-wrap]");
    if (wrapButton) {
      const panel = wrapButton.closest("[data-trace-step-panel]");
      const unwrapped = panel.classList.toggle("is-unwrapped");
      wrapButton.setAttribute("aria-pressed", String(!unwrapped));
      wrapButton.textContent = unwrapped ? "Wrap off" : "Wrap lines";
      return;
    }

    const copyButton = event.target.closest("[data-trace-copy]");
    if (copyButton && navigator.clipboard) {
      const panel = copyButton.closest("[data-trace-step-panel]");
      const visible = [...panel.querySelectorAll("[data-step-detail]")]
        .find((view) => !view.hidden);
      const text = visible?.querySelector("pre")?.textContent || "";
      navigator.clipboard.writeText(text).then(() => {
        copyButton.textContent = "Copied";
        window.setTimeout(() => { if (copyButton.isConnected) copyButton.textContent = "Copy"; }, 900);
      }).catch(() => {});
    }
  });
})();
