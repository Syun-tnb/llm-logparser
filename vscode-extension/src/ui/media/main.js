(() => {
  const vscode = acquireVsCodeApi();

  // Message names mirror the typed contract in src/ui/protocol.ts.
  const logEl = document.getElementById("log");
  const commandSelect = document.getElementById("command");
  const runButton = document.getElementById("run");
  const clearButton = document.getElementById("clear");
  const workspaceRootEl = document.getElementById("workspaceRoot");
  const pageEl = document.querySelector(".page");
  const viewerRefreshButton = document.getElementById("viewer-refresh");
  const viewerFilterInput = document.getElementById("viewer-filter");
  const viewerFileList = document.getElementById("viewer-file-list");
  const viewerThreadMeta = document.getElementById("viewer-thread-meta");
  const viewerMessages = document.getElementById("viewer-messages");
  const viewerRootInput = document.getElementById("viewer-root");

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

  const extensionState = {
    workspaceRoot: "-",
    runState: {
      busy: false,
    },
    viewerState: {
      files: [],
    },
  };

  const uiState = {
    mode: "parse",
    viewerFilter: "",
  };

  let i18nTable = {};
  let viewerConfig = { ...defaultViewerConfig };

  const hasTranslation = (key) =>
    Boolean(i18nTable && Object.prototype.hasOwnProperty.call(i18nTable, key));

  const t = (key, vars = {}, fallback) => {
    const template = i18nTable[key] ?? fallback ?? key;
    return template.replace(/\{(\w+)\}/g, (_, token) => {
      const value = vars[token];
      return value === undefined || value === null ? "" : String(value);
    });
  };

  const applyTranslationToElement = (el, key, attribute) => {
    if (!key) {
      return;
    }
    const translated = t(key);
    if (attribute) {
      el.setAttribute(attribute, translated);
      return;
    }
    el.textContent = translated;
  };

  const translateErrorField = (message, field) => {
    const errorType = message.errorType || "UnknownExecutionError";
    const key = `run.error.${errorType}.${field}`;
    if (hasTranslation(key)) {
      return t(key);
    }

    if (field === "what") {
      return message.what || t("run.error.unknown.what");
    }
    if (field === "why") {
      return message.why || t("run.error.unknown.why");
    }
    return message.nextStep || t("run.error.unknown.nextStep");
  };

  const formatRunFailure = (message) => {
    const errorType = message.errorType || "UnknownExecutionError";
    const titleKey = `run.error.${errorType}.title`;
    const title = hasTranslation(titleKey)
      ? t(titleKey)
      : t("run.error.UnknownExecutionError.title", {}, "Command failed.");

    return [
      title,
      `${t("run.error.label.what")}: ${translateErrorField(message, "what")}`,
      `${t("run.error.label.why")}: ${translateErrorField(message, "why")}`,
      `${t("run.error.label.nextStep")}: ${translateErrorField(message, "nextStep")}`,
    ].join("\n");
  };

  const applyI18n = () => {
    const textTargets = document.querySelectorAll("[data-i18n]");
    textTargets.forEach((el) => {
      const key = el.dataset.i18n;
      if (!key) {
        return;
      }
      applyTranslationToElement(el, key);
    });

    const placeholderTargets = document.querySelectorAll("[data-i18n-placeholder]");
    placeholderTargets.forEach((el) => {
      const key = el.dataset.i18nPlaceholder;
      if (!key) {
        return;
      }
      applyTranslationToElement(el, key, "placeholder");
    });

    const ariaTargets = document.querySelectorAll("[data-i18n-aria-label]");
    ariaTargets.forEach((el) => {
      const key = el.dataset.i18nAriaLabel;
      if (!key) {
        return;
      }
      applyTranslationToElement(el, key, "aria-label");
    });

    document.title = t("app.title");
    document.documentElement.lang = viewerConfig.language || "en";
  };

  const setWorkspaceLabel = () => {
    if (!workspaceRootEl) {
      return;
    }
    workspaceRootEl.textContent = t(
      "workspace.label",
      { path: extensionState.workspaceRoot },
      `Workspace: ${extensionState.workspaceRoot}`
    );
  };

  const applyViewerOptions = () => {
    if (!pageEl) {
      return;
    }
    pageEl.dataset.wrap = viewerConfig.wrap ? "on" : "off";
    pageEl.dataset.compact = viewerConfig.compactMode ? "on" : "off";
    pageEl.dataset.codeTheme = viewerConfig.codeTheme || "auto";
  };

  const applyConfig = (message) => {
    if (message.i18n && typeof message.i18n === "object") {
      i18nTable = message.i18n;
    }
    if (message.config && typeof message.config === "object") {
      viewerConfig = {
        ...defaultViewerConfig,
        ...message.config,
        search: {
          ...defaultViewerConfig.search,
          ...(message.config.search || {}),
        },
      };
    }
    applyViewerOptions();
    applyI18n();
    setWorkspaceLabel();
    renderViewer();
  };

  const showSection = (command) => {
    Object.entries(sections).forEach(([key, element]) => {
      if (!element) {
        return;
      }
      element.classList.toggle("hidden", key !== command);
    });
  };

  const setViewMode = (mode) => {
    uiState.mode = mode;
    if (pageEl) {
      pageEl.dataset.view = mode;
    }

    Object.entries(screens).forEach(([key, element]) => {
      if (!element) {
        return;
      }
      element.classList.toggle("hidden", key !== mode);
    });

    const modeButtons = document.querySelectorAll(".mode-tab");
    modeButtons.forEach((button) => {
      if (!(button instanceof HTMLElement)) {
        return;
      }
      button.classList.toggle("active", button.dataset.view === mode);
    });

    if (mode === "view") {
      requestFileRefresh();
    }
  };

  const postMessage = (message) => {
    vscode.postMessage(message);
  };

  const requestFileRefresh = (root) => {
    postMessage({
      type: "refresh-files",
      payload: root ? { root } : undefined,
    });
  };

  const requestViewerFile = (path) => {
    if (!path) {
      return;
    }
    if (viewerThreadMeta) {
      viewerThreadMeta.textContent = t("viewer.loading");
    }
    if (viewerMessages) {
      viewerMessages.textContent = "";
    }
    postMessage({
      type: "open-viewer-file",
      payload: { path },
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

  const renderViewerFiles = () => {
    if (!viewerFileList) {
      return;
    }

    const files = Array.isArray(extensionState.viewerState.files)
      ? extensionState.viewerState.files
      : [];
    const filterValue = uiState.viewerFilter.trim().toLowerCase();
    const filtered = files.filter((file) => {
      if (!filterValue) {
        return true;
      }
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
      if (file.path === extensionState.viewerState.selectedPath) {
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
        requestViewerFile(file.path);
      });

      viewerFileList.appendChild(item);
    });
  };

  const renderViewerContent = () => {
    if (!viewerThreadMeta || !viewerMessages) {
      return;
    }

    const { error, file } = extensionState.viewerState;
    viewerMessages.textContent = "";

    if (error) {
      const base = t(`viewer.error.${error.code}`, {}, t("viewer.error"));
      const detail = error.detail ? ` (${error.detail})` : "";
      viewerThreadMeta.textContent = `${base}${detail}`;
      return;
    }

    if (!file || !file.meta) {
      viewerThreadMeta.textContent = t("viewer.meta.empty");
      return;
    }

    let messages = Array.isArray(file.messages) ? file.messages : [];
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
    if (file.meta.conversation_id) {
      metaParts.push(t("viewer.meta.thread", { id: file.meta.conversation_id }));
    }
    if (file.meta.provider_id) {
      metaParts.push(t("viewer.meta.provider", { provider: file.meta.provider_id }));
    }
    metaParts.push(t("viewer.meta.count", { count: messages.length }));
    const displayPath = file.display || file.path;
    if (displayPath) {
      metaParts.push(t("viewer.meta.path", { path: displayPath }));
    }
    viewerThreadMeta.textContent =
      metaParts.length > 0 ? metaParts.join(" | ") : t("viewer.meta.empty");

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

  const renderViewer = () => {
    if (viewerRootInput) {
      viewerRootInput.value = extensionState.viewerState.root || "";
    }
    renderViewerFiles();
    renderViewerContent();
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

  const appendLog = (value) => {
    if (!logEl) {
      return;
    }
    logEl.textContent += value;
    logEl.scrollTop = logEl.scrollHeight;
  };

  const extensionMessageHandlers = {
    log(message) {
      appendLog(message.value);
    },
    "clear-log"() {
      if (logEl) {
        logEl.textContent = "";
      }
    },
    "pick-result"(message) {
      if (!message.targetId) {
        return;
      }
      const target = document.getElementById(message.targetId);
      if (target) {
        target.value = message.value ?? "";
      }
      if (message.targetId === "viewer-root") {
        requestFileRefresh(message.value ?? undefined);
      }
    },
    busy(message) {
      extensionState.runState = {
        ...extensionState.runState,
        busy: Boolean(message.value),
      };
      if (runButton) {
        runButton.disabled = extensionState.runState.busy;
      }
    },
    "run-finished"(message) {
      extensionState.runState = {
        busy: false,
        lastExitCode: message.exitCode,
      };
      appendLog(`\n${t("log.exitCode", { code: message.exitCode })}\n`);
    },
    "run-failed"(message) {
      extensionState.runState = {
        busy: false,
        lastError: message,
      };
      appendLog(`\n${formatRunFailure(message)}\n`);
    },
    init(message) {
      extensionState.workspaceRoot = message.workspaceRoot || "-";
      extensionState.runState = message.runState || extensionState.runState;
      extensionState.viewerState = message.viewerState || extensionState.viewerState;
      setWorkspaceLabel();
      renderViewer();
      if (runButton) {
        runButton.disabled = Boolean(extensionState.runState.busy);
      }
    },
    config(message) {
      applyConfig(message);
    },
    "config-changed"(message) {
      applyConfig(message);
    },
    "viewer-state"(message) {
      extensionState.viewerState = message.state || { files: [] };
      renderViewer();
    },
  };

  window.addEventListener("message", (event) => {
    const message = event.data;
    const handler = extensionMessageHandlers[message?.type];
    if (typeof handler === "function") {
      handler(message);
    }
  });

  const pickButtons = document.querySelectorAll("[data-pick]");
  pickButtons.forEach((button) => {
    button.addEventListener("click", () => {
      postMessage({
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
      if (!mode) {
        return;
      }
      setViewMode(mode);
    });
  });

  runButton?.addEventListener("click", () => {
    postMessage({
      type: "run",
      payload: collectPayload(commandSelect?.value ?? "parse"),
    });
  });

  clearButton?.addEventListener("click", () => {
    postMessage({ type: "clear-log" });
  });

  commandSelect?.addEventListener("change", (event) => {
    showSection(event.target.value);
  });

  viewerRefreshButton?.addEventListener("click", () => {
    requestFileRefresh(viewerRootInput?.value.trim() || undefined);
  });

  viewerFilterInput?.addEventListener("input", (event) => {
    uiState.viewerFilter = event.target.value || "";
    renderViewerFiles();
  });

  viewerRootInput?.addEventListener("change", (event) => {
    const target = event.target;
    if (target && typeof target.value === "string") {
      requestFileRefresh(target.value.trim() || undefined);
    }
  });

  applyViewerOptions();
  showSection(commandSelect?.value ?? "parse");
  setViewMode("parse");
})();
