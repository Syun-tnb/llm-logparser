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
  const viewerSearchInput = document.getElementById("viewer-search");
  const viewerRoleFilter = document.getElementById("viewer-role-filter");
  const viewerClearFiltersButton = document.getElementById("viewer-clear-filters");
  const viewerLoadMoreButton = document.getElementById("viewer-load-more");
  const recentTopicsList = document.getElementById("recent-topics-list");
  const resumeCandidatesList = document.getElementById("resume-candidates-list");
  const DEFAULT_MODE = "parse";
  const INITIAL_MESSAGE_LIMIT = 200;
  const LOAD_MORE_STEP = 200;
  const WEBVIEW_STATE_VERSION = 1;

  const screens = {
    parse: document.getElementById("screen-parse"),
    view: document.getElementById("screen-view"),
  };

  const sections = {
    parse: document.getElementById("section-parse"),
    export: document.getElementById("section-export"),
    chain: document.getElementById("section-chain"),
    analyze: document.getElementById("section-analyze"),
  };

  const analyzeSections = {
    stats: document.getElementById("analyze-section-stats"),
    timeline: document.getElementById("analyze-section-timeline"),
    tokens: document.getElementById("analyze-section-tokens"),
    metrics: document.getElementById("analyze-section-metrics"),
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
    salvageState: {
      recentTopics: [],
      resumeCandidates: [],
    },
  };

  const uiState = {
    mode: DEFAULT_MODE,
    viewerFilter: "",
    viewerSearch: "",
    viewerRole: "all",
    viewerVisibleCount: INITIAL_MESSAGE_LIMIT,
    viewerFileKey: "",
    viewerSelectedPath: "",
  };

  const commandFieldIds = {
    parse: {
      provider: "parse-provider",
      input: "parse-input",
      outdir: "parse-outdir",
      dryRun: "parse-dry-run",
      failFast: "parse-fail-fast",
      validateSchema: "parse-validate-schema",
    },
    export: {
      input: "export-input",
      out: "export-out",
      timezone: "export-timezone",
      formatting: "export-formatting",
      split: "export-split",
      splitSoftOverflow: "export-split-soft-overflow",
      splitHard: "export-split-hard",
      splitPreview: "export-split-preview",
      tinyTailThreshold: "export-tiny-tail-threshold",
    },
    chain: {
      provider: "chain-provider",
      input: "chain-input",
      outdir: "chain-outdir",
      timezone: "chain-timezone",
      formatting: "chain-formatting",
      split: "chain-split",
      splitSoftOverflow: "chain-split-soft-overflow",
      splitHard: "chain-split-hard",
      splitPreview: "chain-split-preview",
      tinyTailThreshold: "chain-tiny-tail-threshold",
      exportOutdir: "chain-export-outdir",
      parsedRoot: "chain-parsed-root",
      dryRun: "chain-dry-run",
      failFast: "chain-fail-fast",
      validateSchema: "chain-validate-schema",
    },
    analyze: {
      analyzeCommand: "analyze-subcommand",
      input: "analyze-input",
      perThread: "analyze-stats-per-thread",
      top: "analyze-stats-top",
      sort: "analyze-stats-sort",
      includeRoleBreakdown: "analyze-stats-include-role-breakdown",
      bucket: "analyze-timeline-bucket",
      model: "analyze-tokens-model",
      encoding: "analyze-tokens-encoding",
    },
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

  const getPersistedFields = () => {
    const fields = {};
    document.querySelectorAll("input[id], select[id], textarea[id]").forEach((element) => {
      if (!(element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement)) {
        return;
      }
      if (!element.id) {
        return;
      }
      if (element instanceof HTMLInputElement && element.type === "checkbox") {
        fields[element.id] = element.checked;
        return;
      }
      fields[element.id] = element.value ?? "";
    });
    return fields;
  };

  const applyPersistedFields = (fields) => {
    Object.entries(fields || {}).forEach(([id, value]) => {
      const target = document.getElementById(id);
      if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) {
        return;
      }
      if (target instanceof HTMLInputElement && target.type === "checkbox") {
        target.checked = Boolean(value);
        return;
      }
      target.value = typeof value === "string" ? value : "";
    });
  };

  const normalizePersistedState = (state) => {
    const fields =
      state && typeof state === "object" && state.fields && typeof state.fields === "object"
        ? Object.fromEntries(
            Object.entries(state.fields).filter(
              ([, value]) =>
                typeof value === "string" || typeof value === "boolean"
            )
          )
        : {};

    const viewer =
      state && typeof state === "object" && state.viewer && typeof state.viewer === "object"
        ? state.viewer
        : {};

    const visibleCount =
      typeof viewer.visibleCount === "number" && Number.isFinite(viewer.visibleCount)
        ? Math.max(INITIAL_MESSAGE_LIMIT, Math.floor(viewer.visibleCount))
        : INITIAL_MESSAGE_LIMIT;

    return {
      version:
        state && typeof state === "object" && typeof state.version === "number"
          ? state.version
          : WEBVIEW_STATE_VERSION,
      mode:
        state && typeof state === "object" && state.mode === "view"
          ? "view"
          : DEFAULT_MODE,
      fields,
      viewer: {
        selectedPath:
          typeof viewer.selectedPath === "string" ? viewer.selectedPath : "",
        visibleCount,
      },
    };
  };

  const persistWebviewState = () => {
    const fields = getPersistedFields();
    if (restoreState.viewerSyncPending && restoreState.desiredRoot) {
      fields["viewer-root"] = restoreState.desiredRoot;
    }

    const selectedPath =
      restoreState.viewerSyncPending && restoreState.desiredSelectedPath
        ? restoreState.desiredSelectedPath
        : uiState.viewerSelectedPath || extensionState.viewerState.selectedPath || "";

    const nextState = {
      version: WEBVIEW_STATE_VERSION,
      mode: uiState.mode,
      fields,
      viewer: {
        selectedPath,
        visibleCount: uiState.viewerVisibleCount,
      },
    };
    vscode.setState(nextState);
  };

  const applyPersistedWebviewState = () => {
    applyPersistedFields(persistedState.fields);

    uiState.mode = persistedState.mode;
    uiState.viewerFilter =
      typeof persistedState.fields["viewer-filter"] === "string"
        ? persistedState.fields["viewer-filter"]
        : "";
    uiState.viewerSearch =
      typeof persistedState.fields["viewer-search"] === "string"
        ? persistedState.fields["viewer-search"]
        : "";
    uiState.viewerRole =
      typeof persistedState.fields["viewer-role-filter"] === "string" &&
      persistedState.fields["viewer-role-filter"]
        ? persistedState.fields["viewer-role-filter"]
        : "all";
    uiState.viewerVisibleCount = persistedState.viewer.visibleCount;
    uiState.viewerSelectedPath = persistedState.viewer.selectedPath || "";
    uiState.viewerFileKey = persistedState.viewer.selectedPath || "";

    const restoredCommand =
      typeof persistedState.fields.command === "string" && persistedState.fields.command
        ? persistedState.fields.command
        : commandSelect?.value ?? "parse";

    if (commandSelect) {
      commandSelect.value = restoredCommand;
    }
    showSection(restoredCommand);
    if (restoredCommand === "analyze") {
      showAnalyzeSection(getAnalyzeSubcommand());
    }
    setViewMode(uiState.mode, { refresh: false });
    renderViewerFiles();
  };

  const persistedState = normalizePersistedState(vscode.getState());
  const restoreState = {
    desiredRoot: persistedState.fields["viewer-root"] || "",
    desiredSelectedPath: persistedState.viewer.selectedPath || "",
    rootRequested: false,
    fileRequested: false,
    viewerSyncPending: Boolean(
      persistedState.fields["viewer-root"] || persistedState.viewer.selectedPath
    ),
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

  const getAnalyzeSubcommand = () => valueOf("analyze-subcommand") || "stats";

  const resolvePresetFieldId = (command, name, values = {}) => {
    if (command !== "analyze") {
      return commandFieldIds[command]?.[name];
    }

    const analyzeCommand =
      typeof values.analyzeCommand === "string" && values.analyzeCommand
        ? values.analyzeCommand
        : getAnalyzeSubcommand();

    if (name === "json") {
      return analyzeCommand === "timeline"
        ? "analyze-timeline-json"
        : "analyze-stats-json";
    }
    if (name === "out") {
      return analyzeCommand === "timeline"
        ? "analyze-timeline-out"
        : "analyze-stats-out";
    }
    if (name === "skipExisting") {
      return analyzeCommand === "metrics"
        ? "analyze-metrics-skip-existing"
        : "analyze-tokens-skip-existing";
    }
    if (name === "dryRun") {
      return analyzeCommand === "metrics"
        ? "analyze-metrics-dry-run"
        : "analyze-tokens-dry-run";
    }

    return commandFieldIds.analyze?.[name];
  };

  const showAnalyzeSection = (subcommand) => {
    Object.entries(analyzeSections).forEach(([key, element]) => {
      if (!element) {
        return;
      }
      element.classList.toggle("hidden", key !== subcommand);
    });
  };

  const showSection = (command) => {
    Object.entries(sections).forEach(([key, element]) => {
      if (!element) {
        return;
      }
      element.classList.toggle("hidden", key !== command);
    });

    if (command === "analyze") {
      showAnalyzeSection(getAnalyzeSubcommand());
    }
  };

  const clearFieldValidation = (id) => {
    if (!id) {
      return;
    }
    const field = document.getElementById(id);
    if (!(field instanceof HTMLElement)) {
      return;
    }
    field.removeAttribute("aria-invalid");
    field.closest(".field")?.classList.remove("invalid");
  };

  const clearValidationState = () => {
    Object.values(commandFieldIds).forEach((fields) => {
      Object.values(fields).forEach((id) => clearFieldValidation(id));
    });
  };

  const applyValidationState = (state) => {
    clearValidationState();
    if (!state || !Array.isArray(state.fields) || state.fields.length === 0) {
      return;
    }

    const commandFields = commandFieldIds[state.command] || {};
    let firstInvalidField;

    state.fields.forEach((name) => {
      const id = commandFields[name];
      if (!id) {
        return;
      }
      const field = document.getElementById(id);
      if (!(field instanceof HTMLElement)) {
        return;
      }
      field.setAttribute("aria-invalid", "true");
      field.closest(".field")?.classList.add("invalid");
      firstInvalidField = firstInvalidField || field;
    });

    if (firstInvalidField instanceof HTMLElement) {
      firstInvalidField.focus();
    }
  };

  const setViewMode = (mode, options = {}) => {
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

    if (mode === "view" && options.refresh !== false) {
      requestFileRefresh();
    }

    if (options.persist !== false) {
      persistWebviewState();
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
    uiState.viewerSelectedPath = path;
    if (viewerThreadMeta) {
      viewerThreadMeta.textContent = t("viewer.loading");
    }
    if (viewerMessages) {
      viewerMessages.textContent = "";
    }
    if (viewerLoadMoreButton) {
      viewerLoadMoreButton.classList.add("hidden");
    }
    postMessage({
      type: "open-viewer-file",
      payload: { path },
    });
    persistWebviewState();
  };

  const requestResumeRun = (id) => {
    if (!id) {
      return;
    }
    postMessage({
      type: "resume-run",
      payload: { id },
    });
  };

  const reconcileRestoredViewerState = () => {
    if (!restoreState.viewerSyncPending) {
      return;
    }

    const desiredRoot = restoreState.desiredRoot.trim();
    if (desiredRoot && extensionState.viewerState.root !== desiredRoot) {
      if (!restoreState.rootRequested) {
        restoreState.rootRequested = true;
        requestFileRefresh(desiredRoot);
      }
      return;
    }

    const desiredSelectedPath = restoreState.desiredSelectedPath.trim();
    if (desiredSelectedPath) {
      if (extensionState.viewerState.selectedPath === desiredSelectedPath) {
        uiState.viewerSelectedPath = desiredSelectedPath;
        restoreState.viewerSyncPending = false;
        persistWebviewState();
        return;
      }

      const files = Array.isArray(extensionState.viewerState.files)
        ? extensionState.viewerState.files
        : [];
      if (files.some((file) => file.path === desiredSelectedPath)) {
        if (!restoreState.fileRequested) {
          restoreState.fileRequested = true;
          requestViewerFile(desiredSelectedPath);
        }
        return;
      }
    }

    restoreState.viewerSyncPending = false;
    persistWebviewState();
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

  const escapeHtml = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  const renderMarkdown = (value) => {
    const source =
      typeof value === "string" && value.trim().length > 0
        ? value
        : t("viewer.message.empty");

    try {
      const rendered =
        globalThis.marked && typeof globalThis.marked.parse === "function"
          ? globalThis.marked.parse(source, { gfm: true, breaks: false })
          : "";
      if (
        rendered &&
        globalThis.DOMPurify &&
        typeof globalThis.DOMPurify.sanitize === "function"
      ) {
        return globalThis.DOMPurify.sanitize(rendered);
      }
    } catch (error) {
      // Fall back to plain escaped content if markdown parsing fails.
    }

    return `<p>${escapeHtml(source).replaceAll("\n", "<br />")}</p>`;
  };

  const normalizeRole = (role) =>
    typeof role === "string" ? role.trim().toLowerCase() : "";

  const getViewerFileKey = () =>
    extensionState.viewerState.selectedPath ||
    extensionState.viewerState.file?.path ||
    "";

  const resetViewerThreadState = () => {
    uiState.viewerSearch = "";
    uiState.viewerRole = "all";
    uiState.viewerVisibleCount = INITIAL_MESSAGE_LIMIT;
    if (viewerSearchInput) {
      viewerSearchInput.value = "";
    }
    if (viewerRoleFilter) {
      viewerRoleFilter.value = "all";
    }
  };

  const syncViewerThreadState = () => {
    const nextFileKey = getViewerFileKey();
    if (uiState.viewerFileKey === nextFileKey) {
      return;
    }
    uiState.viewerFileKey = nextFileKey;
    resetViewerThreadState();
  };

  const getThreadMessages = () => {
    const file = extensionState.viewerState.file;
    const allMessages = Array.isArray(file?.messages) ? file.messages : [];
    let filteredMessages = [...allMessages];

    if (!viewerConfig.showSystem) {
      filteredMessages = filteredMessages.filter(
        (message) => normalizeRole(message.role) !== "system"
      );
    }
    if (!viewerConfig.showToolCalls) {
      filteredMessages = filteredMessages.filter(
        (message) => normalizeRole(message.role) !== "tool"
      );
    }
    if (viewerConfig.maxMessagesPerThread > 0) {
      filteredMessages = filteredMessages.slice(-viewerConfig.maxMessagesPerThread);
    }

    if (uiState.viewerRole !== "all") {
      filteredMessages = filteredMessages.filter(
        (message) => normalizeRole(message.role) === uiState.viewerRole
      );
    }

    const query = uiState.viewerSearch.trim().toLowerCase();
    if (query) {
      filteredMessages = filteredMessages.filter((message) => {
        const searchHaystack = [
          message.role,
          message.model,
          message.text,
          formatTimestamp(message.ts),
        ]
          .filter(Boolean)
          .join("\n")
          .toLowerCase();
        return searchHaystack.includes(query);
      });
    }

    const visibleCount = Math.max(uiState.viewerVisibleCount, INITIAL_MESSAGE_LIMIT);
    const visibleMessages = filteredMessages.slice(-visibleCount);

    return {
      allMessages,
      filteredMessages,
      visibleMessages,
      hiddenCount: Math.max(filteredMessages.length - visibleMessages.length, 0),
    };
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

    syncViewerThreadState();

    const { error, file } = extensionState.viewerState;
    viewerMessages.textContent = "";

    if (error) {
      const base = t(
        `viewer.error.${error.code}`,
        {},
        t("viewer.error", { message: error.code || "" }, "Viewer error")
      );
      const detail = error.detail ? ` (${error.detail})` : "";
      viewerThreadMeta.textContent = `${base}${detail}`;
      if (viewerLoadMoreButton) {
        viewerLoadMoreButton.classList.add("hidden");
      }
      return;
    }

    if (!file) {
      viewerThreadMeta.textContent = t("viewer.meta.empty");
      if (viewerLoadMoreButton) {
        viewerLoadMoreButton.classList.add("hidden");
      }
      return;
    }

    const { allMessages, filteredMessages, visibleMessages, hiddenCount } =
      getThreadMessages();

    const metaParts = [];
    if (file.meta?.conversation_id) {
      metaParts.push(t("viewer.meta.thread", { id: file.meta.conversation_id }));
    }
    if (file.meta?.provider_id) {
      metaParts.push(t("viewer.meta.provider", { provider: file.meta.provider_id }));
    }
    metaParts.push(t("viewer.meta.count", { count: allMessages.length }));
    metaParts.push(
      t("viewer.meta.visible", {
        visible: visibleMessages.length,
        filtered: filteredMessages.length,
      })
    );
    const displayPath = file.display || file.path;
    if (displayPath) {
      metaParts.push(t("viewer.meta.path", { path: displayPath }));
    }
    viewerThreadMeta.textContent =
      metaParts.length > 0 ? metaParts.join(" | ") : t("viewer.meta.empty");

    if (visibleMessages.length === 0) {
      const empty = document.createElement("div");
      empty.className = "thread-empty";
      empty.textContent = t("viewer.meta.filteredEmpty");
      viewerMessages.appendChild(empty);
    }

    visibleMessages.forEach((message) => {
      const card = document.createElement("div");
      card.className = "message";
      card.dataset.role = normalizeRole(message.role) || "unknown";

      const header = document.createElement("div");
      header.className = "message-header";

      const roleGroup = document.createElement("div");
      roleGroup.className = "message-heading";

      const role = document.createElement("span");
      role.className = "message-role";
      role.textContent = message.role || "";
      roleGroup.appendChild(role);

      if (message.model) {
        const model = document.createElement("span");
        model.className = "message-model";
        model.textContent = message.model;
        roleGroup.appendChild(model);
      }

      const time = document.createElement("time");
      time.className = "message-time";
      time.textContent = formatTimestamp(message.ts);
      if (message.ts !== undefined && message.ts !== null) {
        const isoDate = new Date(Number(message.ts) * 1000);
        if (!Number.isNaN(isoDate.getTime())) {
          time.dateTime = isoDate.toISOString();
        }
      }

      header.appendChild(roleGroup);
      header.appendChild(time);

      const body = document.createElement("article");
      body.className = "message-body markdown-body";
      body.innerHTML = renderMarkdown(message.text);

      card.appendChild(header);
      card.appendChild(body);
      viewerMessages.appendChild(card);
    });

    if (viewerLoadMoreButton) {
      viewerLoadMoreButton.classList.toggle("hidden", hiddenCount <= 0);
      viewerLoadMoreButton.textContent = t("viewer.toolbar.loadMore", {
        count: Math.min(LOAD_MORE_STEP, hiddenCount),
      });
    }
  };

  const renderSalvageList = (element, items, emptyKey) => {
    if (!element) {
      return;
    }

    element.textContent = "";
    if (!Array.isArray(items) || items.length === 0) {
      const emptyItem = document.createElement("li");
      emptyItem.className = "salvage-empty";
      emptyItem.textContent = t(emptyKey);
      element.appendChild(emptyItem);
      return;
    }

    items.forEach((item) => {
      const listItem = document.createElement("li");

      const button = document.createElement("button");
      button.type = "button";
      button.className = "salvage-item";
      button.addEventListener("click", () => {
        requestResumeRun(item.id);
      });

      const title = document.createElement("div");
      title.className = "salvage-title";
      title.textContent = item.label || "";
      button.appendChild(title);

      if (item.detail) {
        const detail = document.createElement("div");
        detail.className = "salvage-detail";
        detail.textContent = item.detail;
        button.appendChild(detail);
      }

      listItem.appendChild(button);
      element.appendChild(listItem);
    });
  };

  const renderSalvage = () => {
    renderSalvageList(
      recentTopicsList,
      extensionState.salvageState?.recentTopics,
      "viewer.salvage.recent.empty"
    );
    renderSalvageList(
      resumeCandidatesList,
      extensionState.salvageState?.resumeCandidates,
      "viewer.salvage.resume.empty"
    );
  };

  const renderViewer = () => {
    if (viewerRootInput) {
      viewerRootInput.value =
        (restoreState.viewerSyncPending && restoreState.desiredRoot) ||
        extensionState.viewerState.root ||
        "";
    }
    if (viewerSearchInput) {
      viewerSearchInput.value = uiState.viewerSearch;
    }
    if (viewerRoleFilter) {
      viewerRoleFilter.value = uiState.viewerRole;
    }
    renderViewerFiles();
    renderViewerContent();
    renderSalvage();
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
      case "analyze": {
        const analyzeCommand = getAnalyzeSubcommand();
        return {
          command,
          options: {
            analyzeCommand,
            input: valueOf("analyze-input"),
            json:
              analyzeCommand === "stats"
                ? checked("analyze-stats-json")
                : analyzeCommand === "timeline"
                  ? checked("analyze-timeline-json")
                  : false,
            out:
              analyzeCommand === "stats"
                ? valueOf("analyze-stats-out")
                : analyzeCommand === "timeline"
                  ? valueOf("analyze-timeline-out")
                  : "",
            perThread:
              analyzeCommand === "stats" && checked("analyze-stats-per-thread"),
            top:
              analyzeCommand === "stats"
                ? valueOf("analyze-stats-top")
                : "",
            sort:
              analyzeCommand === "stats"
                ? valueOf("analyze-stats-sort")
                : "",
            includeRoleBreakdown:
              analyzeCommand === "stats" &&
              checked("analyze-stats-include-role-breakdown"),
            bucket:
              analyzeCommand === "timeline"
                ? valueOf("analyze-timeline-bucket")
                : "",
            model:
              analyzeCommand === "tokens"
                ? valueOf("analyze-tokens-model")
                : "",
            encoding:
              analyzeCommand === "tokens"
                ? valueOf("analyze-tokens-encoding")
                : "",
            skipExisting:
              analyzeCommand === "tokens"
                ? checked("analyze-tokens-skip-existing")
                : analyzeCommand === "metrics"
                  ? checked("analyze-metrics-skip-existing")
                  : false,
            dryRun:
              analyzeCommand === "tokens"
                ? checked("analyze-tokens-dry-run")
                : analyzeCommand === "metrics"
                  ? checked("analyze-metrics-dry-run")
                  : false,
          },
        };
      }
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
    "apply-run-preset"(message) {
      const preset = message.preset;
      if (!preset || !preset.command) {
        return;
      }
      if (commandSelect) {
        commandSelect.value = preset.command;
      }
      showSection(preset.command);
      setViewMode(DEFAULT_MODE, { refresh: false });
      clearValidationState();

      Object.entries(preset.values || {}).forEach(([name, value]) => {
        const fieldId = resolvePresetFieldId(
          preset.command,
          name,
          preset.values || {}
        );
        if (!fieldId) {
          return;
        }
        const target = document.getElementById(fieldId);
        if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) {
          return;
        }
        if (target instanceof HTMLInputElement && target.type === "checkbox") {
          target.checked = Boolean(value);
        } else {
          target.value = value ?? "";
        }
        clearFieldValidation(fieldId);
      });
      if (preset.command === "analyze") {
        showAnalyzeSection(getAnalyzeSubcommand());
      }
      persistWebviewState();
    },
    "pick-result"(message) {
      if (!message.targetId) {
        return;
      }
      const target = document.getElementById(message.targetId);
      if (target) {
        target.value = message.value ?? "";
      }
      clearFieldValidation(message.targetId);
      persistWebviewState();
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
      clearValidationState();
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
      if (extensionState.viewerState.selectedPath) {
        uiState.viewerSelectedPath = extensionState.viewerState.selectedPath;
      }
      extensionState.salvageState =
        message.salvageState || extensionState.salvageState;
      setWorkspaceLabel();
      renderViewer();
      reconcileRestoredViewerState();
      persistWebviewState();
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
      if (extensionState.viewerState.selectedPath) {
        uiState.viewerSelectedPath = extensionState.viewerState.selectedPath;
      }
      renderViewer();
      reconcileRestoredViewerState();
      persistWebviewState();
    },
    "set-mode"(message) {
      setViewMode(message.mode, { refresh: false });
    },
    "validation-state"(message) {
      applyValidationState(message.state);
    },
    "salvage-state"(message) {
      extensionState.salvageState = message.state || {
        recentTopics: [],
        resumeCandidates: [],
      };
      renderSalvage();
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
    clearValidationState();
    showSection(event.target.value);
    persistWebviewState();
  });

  document.getElementById("analyze-subcommand")?.addEventListener("change", () => {
    clearValidationState();
    showAnalyzeSection(getAnalyzeSubcommand());
    persistWebviewState();
  });

  viewerRefreshButton?.addEventListener("click", () => {
    requestFileRefresh(viewerRootInput?.value.trim() || undefined);
  });

  viewerFilterInput?.addEventListener("input", (event) => {
    uiState.viewerFilter = event.target.value || "";
    renderViewerFiles();
    persistWebviewState();
  });

  viewerSearchInput?.addEventListener("input", (event) => {
    uiState.viewerSearch = event.target.value || "";
    uiState.viewerVisibleCount = INITIAL_MESSAGE_LIMIT;
    renderViewerContent();
    persistWebviewState();
  });

  viewerRoleFilter?.addEventListener("change", (event) => {
    uiState.viewerRole = event.target.value || "all";
    uiState.viewerVisibleCount = INITIAL_MESSAGE_LIMIT;
    renderViewerContent();
    persistWebviewState();
  });

  viewerClearFiltersButton?.addEventListener("click", () => {
    resetViewerThreadState();
    renderViewerContent();
    persistWebviewState();
  });

  viewerLoadMoreButton?.addEventListener("click", () => {
    uiState.viewerVisibleCount += LOAD_MORE_STEP;
    renderViewerContent();
    persistWebviewState();
  });

  viewerRootInput?.addEventListener("change", (event) => {
    const target = event.target;
    if (target && typeof target.value === "string") {
      requestFileRefresh(target.value.trim() || undefined);
    }
  });

  const clearValidationOnEdit = (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.id) {
      return;
    }
    clearFieldValidation(target.id);
    persistWebviewState();
  };

  document.querySelectorAll("input, select, textarea").forEach((element) => {
    element.addEventListener("input", clearValidationOnEdit);
    element.addEventListener("change", clearValidationOnEdit);
  });

  applyViewerOptions();
  applyPersistedWebviewState();
  persistWebviewState();
})();
