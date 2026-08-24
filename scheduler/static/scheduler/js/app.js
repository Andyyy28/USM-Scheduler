(() => {
  "use strict";

  const select = (selector, root = document) => root.querySelector(selector);
  const selectAll = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const menuButton = select(".menu-button");
  const sidebar = select(".sidebar");
  const scrim = select("[data-nav-scrim]");

  const setMenu = (open) => {
    if (!menuButton || !sidebar || !scrim) return;
    document.body.classList.toggle("nav-open", open);
    menuButton.setAttribute("aria-expanded", String(open));
    scrim.hidden = !open;
    if (open) {
      select("a", sidebar)?.focus();
    } else {
      menuButton.focus();
    }
  };

  menuButton?.addEventListener("click", () => {
    setMenu(menuButton.getAttribute("aria-expanded") !== "true");
  });
  scrim?.addEventListener("click", () => setMenu(false));
  sidebar?.addEventListener("click", (event) => {
    if (event.target.closest("a") && window.matchMedia("(max-width: 60rem)").matches) setMenu(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("nav-open")) setMenu(false);
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
    const filter = () => {
      const query = input.value.trim().toLocaleLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        const matches = !query || row.textContent.toLocaleLowerCase().includes(query);
        row.hidden = !matches;
        if (matches) visible += 1;
      });
      if (empty) empty.hidden = visible !== 0;
    };
    input.addEventListener("input", filter);
  });

  selectAll("[data-file-drop]").forEach((dropArea) => {
    const input = document.getElementById(dropArea.getAttribute("for"));
    const name = select("[data-file-name]", dropArea);
    if (!input) return;

    const updateName = () => {
      if (name) name.textContent = input.files?.[0]?.name || "or drag and drop it here";
    };
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
      input.files = event.dataTransfer.files;
      updateName();
    });
  });

  const apiError = (payload, fallback) => {
    if (!payload) return fallback;
    if (typeof payload === "string") return payload;
    if (payload.detail) return payload.detail;
    const first = Object.entries(payload)[0];
    if (!first) return fallback;
    const [field, value] = first;
    const message = Array.isArray(value) ? value.join(" ") : String(value);
    return `${field.replaceAll("_", " ")}: ${message}`;
  };

  selectAll("form[data-api-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const confirmation = event.submitter?.dataset.confirm;
      if (confirmation && !window.confirm(confirmation)) return;
      if (!form.reportValidity()) return;

      const status = select("[data-form-status]", form) || select("[data-form-status]", form.parentElement);
      const submitters = selectAll("button[type='submit'], input[type='submit']", form);
      submitters.forEach((button) => { button.disabled = true; });
      if (status) {
        status.dataset.state = "loading";
        status.textContent = "Submitting…";
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
          status.textContent = "Saved successfully.";
        }
        const destination = form.dataset.successUrl;
        window.setTimeout(() => {
          if (destination) window.location.assign(destination);
          else window.location.reload();
        }, 450);
      } catch (error) {
        if (status) {
          status.dataset.state = "error";
          status.textContent = error instanceof Error ? error.message : "The request could not be completed.";
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
