(() => {
  const vscode = acquireVsCodeApi();

  const logEl = document.getElementById("log");
  const commandSelect = document.getElementById("command");
  const runButton = document.getElementById("run");
  const clearButton = document.getElementById("clear");
  const workspaceRootEl = document.getElementById("workspaceRoot");
  const pageEl = document.querySelector(".page");

  const screens = {
    parse: document.getElementById("screen-parse"),
    view: document.getElementById("screen-view"),
  };

  const sections = {
    parse: document.getElementById("section-parse"),
    export: document.getElementById("section-export"),
    chain: document.getElementById("section-chain"),
  };

  const defaultViewerConfig = {
    language: "en",
    timezone: "local",
    timestampFormat: "absolute",
    wrap: true,
    showSystem: true,
    showToolCalls: true,
    compactMode: false,
    codeTheme: "auto",
    maxMessagesPerThread: 2000,
    search: {
      caseSensitive: false,
      useRegex: false,
    },
  };

  let i18nTable = {};
  let viewerConfig = { ...defaultViewerConfig };
  let workspaceRoot = "-";
  let viewerRoot = "";
  let viewerFiles = [];
  let activeViewerPath = "";
  let lastViewerData = null;

  const t = (key, vars = {}) => {
    const template = i18nTable[key] || key;
    return template.replace(/\{(\w+)\}/g, (_, token) => {
      const value = vars[token];
      return value === undefined || value === null ? "" : String(value);
    });
  };

  const applyI18n = () => {
    const textTargets = document.querySelectorAll("[data-i18n]");
    textTargets.forEach((el) => {
      const key = el.dataset.i18n;
      if (!key) return;
      el.textContent = t(key);
    });

    const placeholderTargets = document.querySelectorAll("[data-i18n-placeholder]");
    placeholderTargets.forEach((el) => {
      const key = el.dataset.i18nPlaceholder;
      if (!key) return;
      el.setAttribute("placeholder", t(key));
    });

    document.title = t("app.title");
  };

  const setWorkspaceLabel = (value) => {
    workspaceRoot = value || "-";
    if (!workspaceRootEl) {
      return;
    }
    workspaceRootEl.textContent = t("workspace.label", { path: workspaceRoot });
  };

  const applyViewerOptions = () => {
    if (!pageEl) {
      return;
    }
    pageEl.dataset.wrap = viewerConfig.wrap ? "on" : "off";
    pageEl.dataset.compact = viewerConfig.compactMode ? "on" : "off";
    pageEl.dataset.codeTheme = viewerConfig.codeTheme || "auto";
  };

  const applyConfig = (payload) => {
    if (!payload) {
      return;
    }
    if (payload.i18n && typeof payload.i18n === "object") {
      i18nTable = payload.i18n;
    }
    if (payload.config && typeof payload.config === "object") {
      const next = payload.config;
      viewerConfig = {
        ...defaultViewerConfig,
        ...next,
        search: {
          ...defaultViewerConfig.search,
          ...(next.search || {}),
        },
      };
    }
    applyViewerOptions();
    applyI18n();
    setWorkspaceLabel(workspaceRoot);
    renderViewerFiles();
    if (lastViewerData) {
      renderViewerContent(lastViewerData);
    }
  };

  const showSection = (command) => {
    Object.entries(sections).forEach(([key, element]) => {
      if (!element) {
        return;
      }
      if (key === command) {
        element.classList.remove("hidden");
      } else {
        element.classList.add("hidden");
      }
    });
  };

  const setViewMode = (mode) => {
    if (!pageEl) {
      return;
    }
    pageEl.dataset.view = mode;
    Object.entries(screens).forEach(([key, element]) => {
      if (!element) {
        return;
      }
      if (key === mode) {
        element.classList.remove("hidden");
      } else {
        element.classList.add("hidden");
      }
    });

    const modeButtons = document.querySelectorAll(".mode-tab");
    modeButtons.forEach((button) => {
      if (!(button instanceof HTMLElement)) {
        return;
      }
      const isActive = button.dataset.view === mode;
      button.classList.toggle("active", isActive);
    });

    if (mode === "view") {
      if (!viewerRoot && workspaceRoot && workspaceRoot !== "-") {
        viewerRoot = workspaceRoot;
        const viewerRootInput = document.getElementById("viewer-root");
        if (viewerRootInput) {
          viewerRootInput.value = viewerRoot;
        }
      }
      requestViewerFiles();
    }
  };

  const pickButtons = document.querySelectorAll("[data-pick]");
  pickButtons.forEach((button) => {
    button.addEventListener("click", () => {
      vscode.postMessage({
        type: "pick",
        payload: {
          kind: button.dataset.pick,
          targetId: button.dataset.target,
        },
      });
    });
  });

  const modeButtons = document.querySelectorAll(".mode-tab");
  modeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const mode = button.dataset.view;
      if (!mode) return;
      setViewMode(mode);
    });
  });

  runButton?.addEventListener("click", () => {
    const payload = collectPayload(commandSelect?.value ?? "parse");
    vscode.postMessage({ type: "run", payload });
  });

  clearButton?.addEventListener("click", () => {
    vscode.postMessage({ type: "clear-log" });
  });

  commandSelect?.addEventListener("change", (event) => {
    showSection(event.target.value);
  });

  const viewerRefreshButton = document.getElementById("viewer-refresh");
  const viewerFilterInput = document.getElementById("viewer-filter");
  const viewerFileList = document.getElementById("viewer-file-list");
  const viewerThreadMeta = document.getElementById("viewer-thread-meta");
  const viewerMessages = document.getElementById("viewer-messages");
  const viewerRootInput = document.getElementById("viewer-root");

  viewerRefreshButton?.addEventListener("click", () => {
    requestViewerFiles();
  });

  viewerFilterInput?.addEventListener("input", () => {
    renderViewerFiles();
  });

  viewerRootInput?.addEventListener("change", (event) => {
    const target = event.target;
    if (target && typeof target.value === "string") {
      viewerRoot = target.value.trim();
      requestViewerFiles();
    }
  });

  const requestViewerFiles = () => {
    vscode.postMessage({
      type: "viewer-list",
      payload: {
        root: viewerRoot || undefined,
      },
    });
  };

  const openViewerFile = (path) => {
    if (!path) {
      return;
    }
    activeViewerPath = path;
    if (viewerThreadMeta) {
      viewerThreadMeta.textContent = t("viewer.loading");
    }
    if (viewerMessages) {
      viewerMessages.textContent = "";
    }
    vscode.postMessage({ type: "viewer-open", payload: { path } });
  };

  const renderViewerFiles = () => {
    if (!viewerFileList) {
      return;
    }

    const filterValue = (viewerFilterInput?.value || "").trim().toLowerCase();
    const filtered = viewerFiles.filter((file) => {
      if (!filterValue) return true;
      const display = (file.display || file.path || "").toLowerCase();
      return display.includes(filterValue);
    });

    viewerFileList.textContent = "";

    if (filtered.length === 0) {
      const emptyItem = document.createElement("li");
      emptyItem.className = "file-item";
      emptyItem.classList.add("empty");
      emptyItem.textContent = t("viewer.files.empty");
      viewerFileList.appendChild(emptyItem);
      return;
    }

    filtered.forEach((file) => {
      const item = document.createElement("li");
      item.className = "file-item";
      if (file.path === activeViewerPath) {
        item.classList.add("active");
      }

      const meta = document.createElement("div");
      meta.className = "file-meta";

      const title = document.createElement("div");
      title.className = "file-title";
      title.textContent = file.name || file.display || file.path || "";

      const pathEl = document.createElement("div");
      pathEl.className = "file-path";
      pathEl.textContent = file.display || file.path || "";

      meta.appendChild(title);
      meta.appendChild(pathEl);
      item.appendChild(meta);

      item.addEventListener("click", () => {
        openViewerFile(file.path);
      });

      viewerFileList.appendChild(item);
    });
  };

  const getLocale = () => {
    if (viewerConfig.language === "ja") {
      return "ja-JP";
    }
    if (viewerConfig.language === "en") {
      return "en-US";
    }
    return undefined;
  };

  const formatAbsoluteTimestamp = (timestamp) => {
    const date = new Date(Number(timestamp) * 1000);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const options = {};
    if (viewerConfig.timezone === "utc") {
      options.timeZone = "UTC";
    }
    return date.toLocaleString(getLocale(), options);
  };

  const formatRelativeTimestamp = (timestamp) => {
    if (typeof Intl === "undefined" || typeof Intl.RelativeTimeFormat === "undefined") {
      return formatAbsoluteTimestamp(timestamp);
    }
    const now = Date.now();
    const target = Number(timestamp) * 1000;
    if (Number.isNaN(target)) {
      return "";
    }
    const diffSeconds = Math.round((target - now) / 1000);
    const absSeconds = Math.abs(diffSeconds);
    const rtf = new Intl.RelativeTimeFormat(getLocale(), { numeric: "auto" });

    if (absSeconds < 60) {
      return rtf.format(diffSeconds, "second");
    }
    const diffMinutes = Math.round(diffSeconds / 60);
    if (Math.abs(diffMinutes) < 60) {
      return rtf.format(diffMinutes, "minute");
    }
    const diffHours = Math.round(diffSeconds / 3600);
    if (Math.abs(diffHours) < 24) {
      return rtf.format(diffHours, "hour");
    }
    const diffDays = Math.round(diffSeconds / 86400);
    if (Math.abs(diffDays) < 30) {
      return rtf.format(diffDays, "day");
    }
    const diffMonths = Math.round(diffSeconds / 2592000);
    if (Math.abs(diffMonths) < 12) {
      return rtf.format(diffMonths, "month");
    }
    const diffYears = Math.round(diffSeconds / 31536000);
    return rtf.format(diffYears, "year");
  };

  const formatTimestamp = (timestamp) => {
    if (timestamp === undefined || timestamp === null || Number.isNaN(timestamp)) {
      return "";
    }
    if (viewerConfig.timestampFormat === "relative") {
      return formatRelativeTimestamp(timestamp);
    }
    return formatAbsoluteTimestamp(timestamp);
  };

  const renderViewerContent = (data) => {
    if (!viewerThreadMeta || !viewerMessages) {
      return;
    }

    lastViewerData = data;
    viewerMessages.textContent = "";

    if (!data || !data.meta) {
      viewerThreadMeta.textContent = t("viewer.meta.empty");
      return;
    }

    let messages = Array.isArray(data.messages) ? data.messages : [];
    if (!viewerConfig.showSystem) {
      messages = messages.filter((message) => message.role !== "system");
    }
    if (!viewerConfig.showToolCalls) {
      messages = messages.filter((message) => message.role !== "tool");
    }
    if (viewerConfig.maxMessagesPerThread > 0) {
      messages = messages.slice(-viewerConfig.maxMessagesPerThread);
    }

    const metaParts = [];
    if (data.meta.conversation_id) {
      metaParts.push(t("viewer.meta.thread", { id: data.meta.conversation_id }));
    }
    if (data.meta.provider_id) {
      metaParts.push(t("viewer.meta.provider", { provider: data.meta.provider_id }));
    }
    metaParts.push(t("viewer.meta.count", { count: messages.length }));
    const displayPath = data.display || data.path;
    if (displayPath) {
      metaParts.push(t("viewer.meta.path", { path: displayPath }));
    }
    viewerThreadMeta.textContent = metaParts.length > 0 ? metaParts.join(" | ") : t("viewer.meta.empty");

    messages.forEach((message) => {
      const card = document.createElement("div");
      card.className = "message";

      const header = document.createElement("div");
      header.className = "message-header";

      const role = document.createElement("div");
      role.className = "message-role";
      role.textContent = message.role || "";

      const time = document.createElement("div");
      time.textContent = formatTimestamp(message.ts);

      header.appendChild(role);
      header.appendChild(time);

      const body = document.createElement("div");
      body.className = "message-text";
      body.textContent = message.text || t("viewer.message.empty");

      card.appendChild(header);
      card.appendChild(body);
      viewerMessages.appendChild(card);
    });
  };

  const collectPayload = (command) => {
    switch (command) {
      case "parse":
        return {
          command,
          options: {
            provider: valueOf("parse-provider"),
            input: valueOf("parse-input"),
            outdir: valueOf("parse-outdir"),
            dryRun: checked("parse-dry-run"),
            failFast: checked("parse-fail-fast"),
            validateSchema: checked("parse-validate-schema"),
          },
        };
      case "export":
        return {
          command,
          options: {
            input: valueOf("export-input"),
            out: valueOf("export-out"),
            timezone: valueOf("export-timezone"),
            formatting: valueOf("export-formatting"),
            split: valueOf("export-split"),
            splitSoftOverflow: valueOf("export-split-soft-overflow"),
            splitHard: checked("export-split-hard"),
            splitPreview: checked("export-split-preview"),
            tinyTailThreshold: valueOf("export-tiny-tail-threshold"),
          },
        };
      case "chain":
        return {
          command,
          options: {
            provider: valueOf("chain-provider"),
            input: valueOf("chain-input"),
            outdir: valueOf("chain-outdir"),
            timezone: valueOf("chain-timezone"),
            formatting: valueOf("chain-formatting"),
            split: valueOf("chain-split"),
            splitSoftOverflow: valueOf("chain-split-soft-overflow"),
            splitHard: checked("chain-split-hard"),
            splitPreview: checked("chain-split-preview"),
            tinyTailThreshold: valueOf("chain-tiny-tail-threshold"),
            exportOutdir: valueOf("chain-export-outdir"),
            parsedRoot: valueOf("chain-parsed-root"),
            dryRun: checked("chain-dry-run"),
            failFast: checked("chain-fail-fast"),
            validateSchema: checked("chain-validate-schema"),
          },
        };
      default:
        return { command: "parse", options: {} };
    }
  };

  const valueOf = (id) => {
    const element = document.getElementById(id);
    if (!element) {
      return "";
    }
    return element.value.trim();
  };

  const checked = (id) => {
    const element = document.getElementById(id);
    if (!element) {
      return false;
    }
    return element.checked;
  };

  window.addEventListener("message", (event) => {
    const message = event.data;
    switch (message.type) {
      case "log":
        appendLog(message.value);
        return;
      case "clear-log":
        if (logEl) {
          logEl.textContent = "";
        }
        return;
      case "pick-result":
        if (message.targetId) {
          const target = document.getElementById(message.targetId);
          if (target) {
            target.value = message.value ?? "";
          }
          if (message.targetId === "viewer-root") {
            viewerRoot = message.value ?? "";
            requestViewerFiles();
          }
        }
        return;
      case "busy":
        if (runButton) {
          runButton.disabled = Boolean(message.value);
        }
        return;
      case "run-error":
        appendLog(`\n${t("log.missingFields", { fields: (message.fields || []).join(", ") })}\n`);
        return;
      case "run-finished":
        appendLog(`\n${t("log.exitCode", { code: message.exitCode })}\n`);
        return;
      case "run-failed":
        appendLog(
          `\n${t("log.runFailed", {
            message: message.message || t("log.unknownError"),
          })}\n`
        );
        return;
      case "init":
        setWorkspaceLabel(message.workspaceRoot || "-");
        return;
      case "config":
      case "config-changed":
        applyConfig(message);
        return;
      case "viewer-files":
        viewerFiles = Array.isArray(message.files) ? message.files : [];
        if (viewerFiles.length === 0) {
          activeViewerPath = "";
          if (viewerThreadMeta) {
            viewerThreadMeta.textContent = t("viewer.meta.empty");
          }
          if (viewerMessages) {
            viewerMessages.textContent = "";
          }
        }
        renderViewerFiles();
        return;
      case "viewer-file":
        activeViewerPath = message.path || "";
        renderViewerFiles();
        renderViewerContent(message);
        return;
      case "viewer-error":
        if (viewerThreadMeta) {
          const codeKey = message.code ? `viewer.error.${message.code}` : "";
          const base = codeKey ? t(codeKey) : t("viewer.error", { message: message.message || "" });
          const detail = message.detail ? ` (${message.detail})` : "";
          viewerThreadMeta.textContent = `${base}${detail}`;
        }
        return;
      default:
        return;
    }
  });

  const appendLog = (value) => {
    if (!logEl) {
      return;
    }
    logEl.textContent += value;
    logEl.scrollTop = logEl.scrollHeight;
  };

  applyViewerOptions();
  applyI18n();
  setWorkspaceLabel("-");
  showSection(commandSelect?.value ?? "parse");
  setViewMode("parse");
})();
