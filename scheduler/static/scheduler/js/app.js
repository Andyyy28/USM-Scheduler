(() => {
  "use strict";

  const select = (selector, root = document) => root.querySelector(selector);
  const selectAll = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const menuButton = select(".menu-button");
  const sidebar = select(".sidebar");
  const scrim = select("[data-nav-scrim]");
  const pageShell = select(".page-shell");
  const mobileNavigation = window.matchMedia("(max-width: 60rem)");

  const menuFocusable = () => selectAll(
    "a[href], button:not([disabled]), summary, input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    sidebar,
  );

  const setMenu = (open, { restoreFocus = true } = {}) => {
    if (!menuButton || !sidebar || !scrim) return;
    const shouldOpen = Boolean(open && mobileNavigation.matches);
    document.body.classList.toggle("nav-open", shouldOpen);
    menuButton.setAttribute("aria-expanded", String(shouldOpen));
    select(".visually-hidden", menuButton)?.replaceChildren(
      document.createTextNode(shouldOpen ? "Close navigation" : "Open navigation"),
    );
    scrim.hidden = !shouldOpen;
    if (shouldOpen) {
      if (pageShell) {
        pageShell.setAttribute("aria-hidden", "true");
        pageShell.inert = true;
      }
      menuFocusable()[0]?.focus();
    } else {
      if (pageShell) {
        pageShell.removeAttribute("aria-hidden");
        pageShell.inert = false;
      }
      if (restoreFocus && mobileNavigation.matches) menuButton.focus();
    }
  };

  menuButton?.addEventListener("click", () => {
    setMenu(menuButton.getAttribute("aria-expanded") !== "true");
  });
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
  mobileNavigation.addEventListener("change", (event) => {
    if (!event.matches) setMenu(false, { restoreFocus: false });
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

  const apiError = (payload, fallback) => {
    if (!payload) return fallback;
    if (typeof payload === "string") return payload;
    if (payload.detail) return payload.detail;
    const messages = Object.entries(payload).map(([field, value]) => {
      const message = Array.isArray(value) ? value.join(" ") : String(value);
      return `${field.replaceAll("_", " ")}: ${message}`;
    });
    return messages.length ? messages.join(" ") : fallback;
  };

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
        if (!response.ok) throw new Error(apiError(payload, `Request failed (${response.status}).`));
        if (status) {
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
          status.textContent = error instanceof Error ? error.message : "The request could not be completed.";
          status.setAttribute("tabindex", "-1");
          status.focus();
        } else {
          window.alert(error instanceof Error ? error.message : "The request could not be completed.");
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
