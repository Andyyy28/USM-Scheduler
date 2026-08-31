(() => {
  "use strict";

  const select = (selector, root = document) => root.querySelector(selector);
  const selectAll = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const menuButton = select(".menu-button");
  const sidebar = select(".sidebar");
  const closeButton = select("[data-nav-close]", sidebar || document);
  const scrim = select("[data-nav-scrim]");
  const pageShell = select(".page-shell");
  const mobileNavigation = window.matchMedia("(max-width: 74rem)");
  let menuOpener = menuButton;

  const menuFocusable = () => selectAll(
    "a[href], button:not([disabled]), summary, input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    sidebar,
  );

  const setMenu = (open, { restoreFocus = true } = {}) => {
    if (!menuButton || !sidebar || !scrim) return;
    const wasOpen = document.body.classList.contains("nav-open");
    const shouldOpen = Boolean(open && mobileNavigation.matches);
    document.body.classList.toggle("nav-open", shouldOpen);
    menuButton.setAttribute("aria-expanded", String(shouldOpen));
    select(".visually-hidden", menuButton)?.replaceChildren(
      document.createTextNode(shouldOpen ? "Close navigation" : "Open navigation"),
    );
    scrim.hidden = !shouldOpen;
    if (shouldOpen) {
      menuOpener = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : menuButton;
      sidebar.removeAttribute("aria-hidden");
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
      sidebar.inert = false;
      if (pageShell) {
        pageShell.setAttribute("aria-hidden", "true");
        pageShell.inert = true;
      }
      closeButton?.focus();
    } else {
      if (pageShell) {
        pageShell.removeAttribute("aria-hidden");
        pageShell.inert = false;
      }
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
      if (mobileNavigation.matches) {
        sidebar.setAttribute("aria-hidden", "true");
        sidebar.inert = true;
      } else {
        sidebar.removeAttribute("aria-hidden");
        sidebar.inert = false;
      }
      if (restoreFocus && wasOpen && mobileNavigation.matches) menuOpener?.focus();
    }
  };

  const syncNavigationMode = () => {
    setMenu(false, { restoreFocus: false });
  };

  menuButton?.addEventListener("click", () => {
    setMenu(menuButton.getAttribute("aria-expanded") !== "true");
  });
  closeButton?.addEventListener("click", () => setMenu(false));
  scrim?.addEventListener("click", () => setMenu(false));
  sidebar?.addEventListener("click", (event) => {
    if (event.target.closest("a") && mobileNavigation.matches) setMenu(false, { restoreFocus: false });
  });
  document.addEventListener("keydown", (event) => {
    if (!document.body.classList.contains("nav-open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setMenu(false);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = menuFocusable();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  mobileNavigation.addEventListener("change", syncNavigationMode);
  syncNavigationMode();

  const tableWrappers = selectAll(".table-wrap");
  const updateTableOverflow = (wrapper) => {
    const table = select("table", wrapper);
    if (!table) return;
    const isOverflowing = wrapper.scrollWidth > wrapper.clientWidth + 1;
    const heading = wrapper.closest(".panel")?.querySelector("h2, h3")?.textContent.trim();
    let captionElement = select("caption", table);
    if (!captionElement) {
      captionElement = document.createElement("caption");
      captionElement.className = "visually-hidden";
      captionElement.dataset.generatedCaption = "true";
      captionElement.textContent = heading || "Data table";
      table.prepend(captionElement);
    }
    const label = captionElement.textContent.trim() || "Data table";
    let cue = wrapper.nextElementSibling;
    if (!cue?.matches(".table-scroll-cue[data-table-scroll-cue]")) {
      cue = document.createElement("p");
      cue.className = "table-scroll-cue";
      cue.dataset.tableScrollCue = "";
      cue.setAttribute("aria-hidden", "true");
      cue.textContent = "Scroll horizontally to see all columns \u2192";
      wrapper.insertAdjacentElement("afterend", cue);
    }

    wrapper.dataset.overflow = String(isOverflowing);
    cue.hidden = !isOverflowing;
    if (isOverflowing) {
      if (!wrapper.hasAttribute("tabindex")) {
        wrapper.tabIndex = 0;
        wrapper.dataset.managedTabindex = "true";
      }
      if (!wrapper.hasAttribute("aria-label")) {
        wrapper.setAttribute("aria-label", `Scrollable table: ${label}`);
        wrapper.dataset.managedAriaLabel = "true";
      }
      if (!wrapper.hasAttribute("role")) {
        wrapper.setAttribute("role", "region");
        wrapper.dataset.managedRole = "true";
      }
    } else {
      if (wrapper.dataset.managedTabindex === "true") wrapper.removeAttribute("tabindex");
      if (wrapper.dataset.managedAriaLabel === "true") wrapper.removeAttribute("aria-label");
      if (wrapper.dataset.managedRole === "true") wrapper.removeAttribute("role");
      delete wrapper.dataset.managedTabindex;
      delete wrapper.dataset.managedAriaLabel;
      delete wrapper.dataset.managedRole;
    }
  };

  if (tableWrappers.length) {
    const refreshTableOverflow = () => tableWrappers.forEach(updateTableOverflow);
    if ("ResizeObserver" in window) {
      const tableResizeObserver = new ResizeObserver((entries) => {
        entries.forEach(({ target }) => updateTableOverflow(target));
      });
      tableWrappers.forEach((wrapper) => tableResizeObserver.observe(wrapper));
    } else {
      window.addEventListener("resize", refreshTableOverflow);
    }
    window.addEventListener("load", refreshTableOverflow, { once: true });
    window.requestAnimationFrame(refreshTableOverflow);
  }

  selectAll("[data-benchmark-chart]").forEach((chart) => {
    const bars = selectAll("[data-benchmark-value]", chart);
    const whiskers = selectAll("[data-benchmark-low][data-benchmark-high]", chart);
    const finiteNumber = (value) => {
      const parsed = Number.parseFloat(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const observedValues = [
      ...bars.map((bar) => finiteNumber(bar.dataset.benchmarkValue)),
      ...whiskers.map((whisker) => finiteNumber(whisker.dataset.benchmarkHigh)),
    ].filter((value) => value !== null && value >= 0);
    const observedMaximum = observedValues.length ? Math.max(...observedValues) : 0;
    const declaredMaximum = finiteNumber(chart.dataset.scaleMax);
    const scaleMaximum = declaredMaximum !== null && declaredMaximum > 0
      ? declaredMaximum
      : (observedMaximum > 0 ? observedMaximum : 1);
    const percent = (value) => `${Math.max(0, Math.min(100, value / scaleMaximum * 100))}%`;

    bars.forEach((bar) => {
      const value = finiteNumber(bar.dataset.benchmarkValue);
      if (value !== null) bar.style.setProperty("--benchmark-bar-size", percent(value));
    });
    whiskers.forEach((whisker) => {
      const low = finiteNumber(whisker.dataset.benchmarkLow);
      const high = finiteNumber(whisker.dataset.benchmarkHigh);
      if (low === null || high === null) return;
      const lower = Math.min(low, high);
      const upper = Math.max(low, high);
      whisker.style.setProperty("--benchmark-whisker-start", percent(lower));
      whisker.style.setProperty(
        "--benchmark-whisker-size",
        percent(Math.max(0, upper - lower)),
      );
    });

    const scaleLabel = select("[data-benchmark-scale-label]", chart);
    if (scaleLabel && declaredMaximum === null) {
      scaleLabel.textContent = observedMaximum > 0
        ? `Observed maximum ${new Intl.NumberFormat("en", { maximumFractionDigits: 3 }).format(observedMaximum)}`
        : (observedValues.length ? "All observed values 0" : "No feasible values");
    }
    chart.dataset.enhanced = "true";
  });

  selectAll(".message__dismiss").forEach((button) => {
    button.addEventListener("click", () => button.closest(".message")?.remove());
  });

  selectAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.getAttribute("aria-controls"));
      if (!input) return;
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      button.textContent = reveal ? "Hide" : "Show";
      button.setAttribute("aria-pressed", String(reveal));
    });
  });

  selectAll("[data-auto-submit]").forEach((control) => {
    control.addEventListener("change", () => control.form?.requestSubmit());
  });

  selectAll("[data-table-search]").forEach((input) => {
    const table = document.getElementById(input.dataset.tableSearch);
    if (!table) return;
    const rows = selectAll("tbody tr", table);
    const empty = table.closest(".panel")?.querySelector("[data-table-empty]");
    const status = table.closest(".panel")?.querySelector("[data-table-search-status]");
    input.setAttribute("aria-controls", table.id);
    const filter = () => {
      const query = input.value.trim().toLocaleLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        const matches = !query || row.textContent.toLocaleLowerCase().includes(query);
        row.hidden = !matches;
        if (matches) visible += 1;
      });
      if (empty) empty.hidden = visible !== 0;
      if (status) {
        const noun = visible === 1 ? "row" : "rows";
        status.textContent = query
          ? `${visible} ${noun} match “${input.value.trim()}”.`
          : `${rows.length} ${rows.length === 1 ? "row" : "rows"} available.`;
      }
    };
    input.addEventListener("input", filter);
    filter();
  });

  selectAll("[data-file-drop]").forEach((dropArea) => {
    const input = document.getElementById(dropArea.getAttribute("for"));
    const name = select("[data-file-name]", dropArea);
    if (!input) return;

    const updateName = () => {
      const selectedName = input.files?.[0]?.name;
      if (name) name.textContent = selectedName ? `Selected: ${selectedName}` : "or drag and drop it here";
      dropArea.dataset.hasFile = String(Boolean(selectedName));
    };
    dropArea.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      input.click();
    });
    input.addEventListener("change", updateName);
    ["dragenter", "dragover"].forEach((eventName) => {
      dropArea.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropArea.dataset.dragging = "true";
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropArea.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropArea.dataset.dragging = "false";
      });
    });
    dropArea.addEventListener("drop", (event) => {
      if (!event.dataTransfer?.files?.length) return;
      try {
        input.files = event.dataTransfer.files;
      } catch (_error) {
        input.click();
        return;
      }
      updateName();
      dropArea.focus();
    });
  });

  selectAll("[data-print-schedule], [data-print-page]").forEach((button) => {
    button.addEventListener("click", () => window.print());
  });

  const normalizeApiError = (payload, fallback) => {
    const result = { summary: fallback, items: [] };
    const label = (path) => path.map((part) => part.replaceAll("_", " ")).join(" / ");
    const visit = (value, path = []) => {
      if (value === null || value === undefined || value === "") return;
      if (["string", "number", "boolean"].includes(typeof value)) {
        const message = `${value}`;
        result.items.push(path.length ? `${label(path)}: ${message}` : message);
        return;
      }
      if (Array.isArray(value)) {
        value.forEach((item) => visit(item, path));
        return;
      }
      if (typeof value !== "object") return;
      if (typeof value.message === "string") {
        const prefix = typeof value.code === "string" ? `${value.code}: ` : "";
        result.items.push(`${prefix}${value.message}`);
        return;
      }
      Object.entries(value).forEach(([field, nested]) => visit(nested, [...path, field]));
    };

    if (typeof payload === "string") return { summary: payload, items: [] };
    if (Array.isArray(payload)) {
      visit(payload);
      return result;
    }
    if (!payload || typeof payload !== "object") return result;
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      result.summary = payload.detail;
    }
    if (Array.isArray(payload.issues)) payload.issues.forEach((issue) => visit(issue));
    Object.entries(payload).forEach(([field, value]) => {
      if (["detail", "issues", "code"].includes(field)) return;
      visit(value, [field]);
    });
    if (result.summary === fallback && typeof payload.code === "string") {
      result.summary = payload.code.replaceAll("_", " ");
    }
    return result;
  };

  const renderApiError = (status, normalized) => {
    status.replaceChildren();
    const summary = document.createElement("p");
    summary.textContent = normalized.summary;
    status.append(summary);
    if (normalized.items.length) {
      const list = document.createElement("ul");
      normalized.items.forEach((message) => {
        const item = document.createElement("li");
        item.textContent = message;
        list.append(item);
      });
      status.append(list);
    }
  };

  selectAll("[data-dataset-select]").forEach((datasetSelect) => {
    const details = document.getElementById(datasetSelect.getAttribute("aria-controls"));
    if (!details) return;
    const updateDetails = () => {
      const option = datasetSelect.selectedOptions[0];
      const selected = Boolean(option?.value);
      details.hidden = !selected;
      if (!selected) return;
      selectAll("[data-dataset-field]", details).forEach((field) => {
        const key = field.dataset.datasetField;
        field.textContent = option.dataset[key] || "Not recorded";
      });
    };
    datasetSelect.addEventListener("change", updateDetails);
    updateDetails();
  });

  selectAll("form[data-api-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const confirmation = event.submitter?.dataset.confirm;
      if (confirmation && !window.confirm(confirmation)) return;
      if (!form.reportValidity()) return;

      const status = select("[data-form-status]", form) || select("[data-form-status]", form.parentElement);
      const submitters = selectAll("button[type='submit'], input[type='submit']", form);
      selectAll("[aria-invalid='true']", form).forEach((field) => field.removeAttribute("aria-invalid"));
      form.setAttribute("aria-busy", "true");
      submitters.forEach((button) => { button.disabled = true; });
      if (status) {
        status.removeAttribute("role");
        status.dataset.state = "loading";
        status.textContent = "Working… Please keep this page open.";
      }

      try {
        const method = (form.dataset.method || form.method || "POST").toUpperCase();
        const csrf = select("input[name='csrfmiddlewaretoken']", form)?.value || "";
        const response = await fetch(form.action, {
          method,
          body: new FormData(form),
          credentials: "same-origin",
          headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" },
        });
        const contentType = response.headers.get("content-type") || "";
        const payload = contentType.includes("application/json") ? await response.json() : await response.text();
        if (!response.ok) {
          const normalized = normalizeApiError(payload, `Request failed (${response.status}).`);
          const requestError = new Error(normalized.summary);
          requestError.apiDetails = normalized;
          throw requestError;
        }
        if (status) {
          status.removeAttribute("role");
          status.dataset.state = "success";
          status.textContent = form.dataset.successMessage || "Request completed successfully.";
        }
        form.removeAttribute("aria-busy");
        const destination = form.dataset.successUrl;
        window.setTimeout(() => {
          if (destination) window.location.assign(destination);
          else window.location.reload();
        }, 450);
      } catch (error) {
        form.removeAttribute("aria-busy");
        if (status) {
          status.dataset.state = "error";
          status.setAttribute("role", "alert");
          const normalized = error?.apiDetails || {
            summary: error instanceof Error ? error.message : "The request could not be completed.",
            items: [],
          };
          renderApiError(status, normalized);
          status.setAttribute("tabindex", "-1");
          status.focus();
        } else {
          const normalized = error?.apiDetails || {
            summary: error instanceof Error ? error.message : "The request could not be completed.",
            items: [],
          };
          window.alert([normalized.summary, ...normalized.items].join("\n"));
        }
        submitters.forEach((button) => { button.disabled = false; });
      }
    });
  });

  selectAll("[data-clone-term-form]").forEach((form) => {
    const source = select("[data-clone-source]", form);
    const syncAction = () => { form.action = source?.value || ""; };
    source?.addEventListener("change", syncAction);
    syncAction();
  });

  selectAll("[data-finalize-revision-form]").forEach((form) => {
    const source = select("[data-finalize-source]", form);
    const syncAction = () => { form.action = source?.value || ""; };
    source?.addEventListener("change", syncAction);
    syncAction();
  });

  const comparisonForm = select("[data-comparison-form]");
  comparisonForm?.addEventListener("submit", (event) => {
    const left = select("[name='left']", comparisonForm);
    const right = select("[name='right']", comparisonForm);
    const status = select("[data-comparison-status]");
    if (left?.value && left.value === right?.value) {
      event.preventDefault();
      if (status) {
        status.dataset.state = "error";
        status.textContent = "Choose two different runs.";
      }
      right.focus();
    }
  });
})();
