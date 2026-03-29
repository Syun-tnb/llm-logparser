import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import * as readline from "readline";
import {
  createRunCliRequest,
  formatCliCommandLine,
  getInvalidCliFields,
  InvalidInputError,
  runCli,
  toCliUiError,
  type CliRunPayload,
} from "../backend/python";
import type {
  ApplyRunPresetMessage,
  ExtensionToWebviewMessage,
  OpenViewerFileMessage,
  PickMessage,
  RefreshFilesMessage,
  ResumeRunMessage,
  RunState,
  SalvageState,
  SalvageStateMessage,
  ValidationStateMessage,
  ViewerConfig,
  ViewerErrorCode,
  ViewerFileData,
  ViewerMessage,
  ViewerState,
  ViewerStateMessage,
  WebviewToExtensionMessage,
} from "./protocol";

type PanelRuntime = {
  createRunCliRequest: typeof createRunCliRequest;
  formatCliCommandLine: typeof formatCliCommandLine;
  getInvalidCliFields: typeof getInvalidCliFields;
  runCli: typeof runCli;
  toCliUiError: typeof toCliUiError;
};

const defaultPanelRuntime: PanelRuntime = {
  createRunCliRequest,
  formatCliCommandLine,
  getInvalidCliFields,
  runCli,
  toCliUiError,
};

let panelRuntime: PanelRuntime = { ...defaultPanelRuntime };

export class LogParserPanel {
  public static currentPanel: LogParserPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly extensionUri: vscode.Uri;
  private readonly outputChannel: vscode.OutputChannel;
  private disposables: vscode.Disposable[] = [];
  private workspaceRoot?: string;
  private runState: RunState;
  private viewerState: ViewerState;
  private pendingRunPreset?: ApplyRunPresetMessage["preset"];
  private runHistory: RunHistoryEntry[] = [];

  private constructor(
    panel: vscode.WebviewPanel,
    extensionUri: vscode.Uri,
    outputChannel: vscode.OutputChannel
  ) {
    this.panel = panel;
    this.extensionUri = extensionUri;
    this.outputChannel = outputChannel;
    this.workspaceRoot = this.getWorkspaceRoot();
    this.runState = {
      busy: false,
    };
    this.viewerState = {
      root: this.workspaceRoot,
      files: [],
    };

    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      (message) => this.handleMessage(message as WebviewToExtensionMessage),
      null,
      this.disposables
    );
    this.disposables.push(
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("llmLogparser.viewer")) {
          void this.postConfig("config-changed");
        }
      })
    );

    this.panel.webview.html = this.getHtmlForWebview();
    this.postInit();
  }

  public static createOrShow(
    extensionUri: vscode.Uri,
    outputChannel: vscode.OutputChannel
  ): LogParserPanel {
    const column = vscode.window.activeTextEditor
      ? vscode.window.activeTextEditor.viewColumn
      : undefined;

    if (LogParserPanel.currentPanel) {
      LogParserPanel.currentPanel.panel.reveal(column);
      return LogParserPanel.currentPanel;
    }

    const panel = vscode.window.createWebviewPanel(
      "llmLogparserPanel",
      "LLM Logparser",
      column ?? vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: getWebviewResourceRoots(extensionUri),
      }
    );

    LogParserPanel.currentPanel = new LogParserPanel(
      panel,
      extensionUri,
      outputChannel
    );
    return LogParserPanel.currentPanel;
  }

  public showWithInput(filePath: string): void {
    this.pendingRunPreset = {
      command: "parse",
      values: {
        input: filePath,
      },
    };
    this.postMessage({
      type: "set-mode",
      mode: "parse",
    });
    this.postMessage({
      type: "apply-run-preset",
      preset: this.pendingRunPreset,
    });
  }

  public dispose(): void {
    LogParserPanel.currentPanel = undefined;
    this.panel.dispose();

    while (this.disposables.length) {
      const disposable = this.disposables.pop();
      if (disposable) {
        disposable.dispose();
      }
    }
  }
  
  private resolveExecutionRoot(): string {
    const config = vscode.workspace.getConfiguration("llmLogparser");
    const configuredRoot = config.get<string>("defaultRoot");

    return (
      this.viewerState.root ||
      configuredRoot ||
      this.workspaceRoot ||
      process.cwd()
    );
  }

  private postInit(): void {
    this.syncWorkspaceRoot();
    this.postMessage({
      type: "init",
      workspaceRoot: this.workspaceRoot,
      runState: this.runState,
      viewerState: this.viewerState,
      salvageState: this.getSalvageState(),
    });
    if (this.pendingRunPreset) {
      this.postMessage({
        type: "apply-run-preset",
        preset: this.pendingRunPreset,
      });
    }
    void this.postConfig("config");
  }

  private async handleMessage(message: WebviewToExtensionMessage) {
    switch (message.type) {
      case "pick":
        await this.handlePick(message.payload);
        return;
      case "run":
        await this.handleRun(message.payload);
        return;
      case "refresh-files":
        await this.handleRefreshFiles(message.payload);
        return;
      case "open-viewer-file":
        await this.handleViewerOpen(message.payload);
        return;
      case "clear-log":
        this.postMessage({ type: "clear-log" });
        return;
      case "resume-run":
        this.handleResumeRun(message.payload);
        return;
      default:
        return;
    }
  }

  private getWorkspaceRoot(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  }

  private syncWorkspaceRoot(): void {
    this.workspaceRoot = this.getWorkspaceRoot();
    if (!this.viewerState.root && this.workspaceRoot) {
      this.viewerState.root = this.workspaceRoot;
    }
  }

  private postMessage(message: ExtensionToWebviewMessage): void {
    void this.panel.webview.postMessage(message);
  }

  private postViewerState(): void {
    const message: ViewerStateMessage = {
      type: "viewer-state",
      state: this.viewerState,
    };
    this.postMessage(message);
  }

  private postSalvageState(): void {
    const message: SalvageStateMessage = {
      type: "salvage-state",
      state: this.getSalvageState(),
    };
    this.postMessage(message);
  }

  private postValidationState(
    command: CliRunPayload["command"],
    fields: string[]
  ): void {
    const message: ValidationStateMessage = {
      type: "validation-state",
      state: {
        command,
        fields,
      },
    };
    this.postMessage(message);
  }

  private setBusy(value: boolean): void {
    this.runState = {
      ...this.runState,
      busy: value,
    };
    this.postMessage({ type: "busy", value });
  }

  private appendExecutionLog(value: string): void {
    this.postMessage({ type: "log", value });
    this.outputChannel.append(value);
  }

  private appendOutputLine(value: string): void {
    this.outputChannel.appendLine(value);
  }

  private recordRunHistory(
    payload: CliRunPayload,
    result: {
      success: boolean;
      exitCode?: number;
      commandLine?: string;
      errorWhat?: string;
    }
  ): void {
    const entry: RunHistoryEntry = {
      id: `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      ts: Date.now(),
      payload,
      success: result.success,
      exitCode: result.exitCode,
      commandLine: result.commandLine,
      errorWhat: result.errorWhat,
    };

    this.runHistory = [entry, ...this.runHistory].slice(0, 20);
    this.postSalvageState();
  }

  private getSalvageState(): SalvageState {
    const recentTopics: SalvageState["recentTopics"] = [];
    const seenLabels = new Set<string>();

    for (const entry of this.runHistory) {
      const label = buildRunHistoryLabel(entry.payload);
      const normalized = label.toLowerCase();
      if (seenLabels.has(normalized)) {
        continue;
      }
      seenLabels.add(normalized);
      recentTopics.push({
        id: entry.id,
        label,
        detail: buildRunHistoryDetail(entry),
        timestamp: entry.ts,
      });
      if (recentTopics.length >= 6) {
        break;
      }
    }

    const resumeCandidates = this.runHistory
      .filter((entry) => !entry.success)
      .slice(0, 6)
      .map((entry) => ({
        id: entry.id,
        label: buildRunHistoryLabel(entry.payload),
        detail: buildRunHistoryDetail(entry),
        timestamp: entry.ts,
      }));

    return {
      recentTopics,
      resumeCandidates,
    };
  }

  private handleResumeRun(payload: ResumeRunMessage["payload"]): void {
    const entry = this.runHistory.find((item) => item.id === payload.id);
    if (!entry) {
      return;
    }

    this.postMessage({
      type: "set-mode",
      mode: "parse",
    });
    this.postMessage({
      type: "apply-run-preset",
      preset: createRunPreset(entry.payload),
    });
  }

  private async promptToOpenOutput(uiError: {
    what: string;
  }): Promise<void> {
    const openOutput = "Open Output";
    const selection = await vscode.window.showErrorMessage(
      uiError.what,
      openOutput
    );
    if (selection === openOutput) {
      this.outputChannel.show(true);
    }
  }

  private async handlePick(payload: PickMessage["payload"]): Promise<void> {
    const options: vscode.OpenDialogOptions = {
      canSelectMany: false,
      canSelectFolders: payload.kind === "folder",
      canSelectFiles: payload.kind === "file",
      openLabel: "Select",
    };

    const result = await vscode.window.showOpenDialog(options);
    if (!result || result.length === 0) {
      return;
    }

    this.postMessage({
      type: "pick-result",
      targetId: payload.targetId,
      value: result[0].fsPath,
    });
  }

  private async handleRun(payload: CliRunPayload): Promise<void> {
    this.syncWorkspaceRoot();
    const executionRoot = this.resolveExecutionRoot();
    const config = vscode.workspace.getConfiguration("llmLogparser");
    const pythonPath = config.get<string>("pythonPath") ?? "python3";
    const cliCommand = config.get<string>("cliCommand") ?? "";
    const invalidFields = panelRuntime.getInvalidCliFields(payload);

    this.postValidationState(payload.command, invalidFields);
    if (invalidFields.length > 0) {
      const missing = invalidFields.join(", ");
      const uiError = panelRuntime.toCliUiError(
        new InvalidInputError(
          "preflight",
          "Required command inputs are missing.",
          `The ${payload.command} command needs these fields before it can run: ${missing}.`,
          `Fill in ${missing} in the panel and run the command again.`
        )
      );
      this.runState = {
        busy: false,
        lastError: uiError,
      };
      this.postMessage({
        type: "run-failed",
        errorType: uiError.type,
        what: uiError.what,
        why: uiError.why,
        nextStep: uiError.nextStep,
      });
      this.recordRunHistory(payload, {
        success: false,
        errorWhat: uiError.what,
      });
      return;
    }

    try {
      const runRequest = panelRuntime.createRunCliRequest(payload);
      const commandLine = await panelRuntime.formatCliCommandLine(runRequest, {
        cwd: executionRoot,
        pythonPath,
        cliCommand,
      });

      this.runState = {
        busy: true,
      };
      this.setBusy(true);
      this.appendExecutionLog(`> ${commandLine}\n`);

      const exitCode = await panelRuntime.runCli(runRequest, {
        cwd: executionRoot,
        pythonPath,
        cliCommand,
        onStdout: (chunk) => this.appendExecutionLog(chunk),
        onStderr: (chunk) => this.appendExecutionLog(chunk),
      });

      this.runState = {
        busy: false,
        lastExitCode: exitCode,
      };
      this.recordRunHistory(payload, {
        success: true,
        exitCode,
        commandLine,
      });
      this.appendOutputLine("");
      this.appendOutputLine(`Exit code: ${exitCode}`);
      if (payload.command === "parse" || payload.command === "chain") {
        await this.handleRefreshFiles({
          root: this.getViewerRootForCommand(payload),
        });
        this.postMessage({
          type: "set-mode",
          mode: "view",
        });
      }
      this.postMessage({
        type: "run-finished",
        exitCode,
      });
    } catch (error) {
      const uiError = panelRuntime.toCliUiError(error);
      this.runState = {
        busy: false,
        lastError: uiError,
      };
      this.appendOutputLine("");
      this.appendOutputLine(`Command failed: ${uiError.what}`);
      this.appendOutputLine(`Why: ${uiError.why}`);
      this.appendOutputLine(`Next step: ${uiError.nextStep}`);
      this.postMessage({
        type: "run-failed",
        errorType: uiError.type,
        what: uiError.what,
        why: uiError.why,
        nextStep: uiError.nextStep,
      });
      this.recordRunHistory(payload, {
        success: false,
        errorWhat: uiError.what,
      });
      void this.promptToOpenOutput(uiError);
    } finally {
      this.setBusy(false);
    }
  }

  private getViewerRootForCommand(payload: CliRunPayload): string | undefined {
    const baseRoot = this.resolveExecutionRoot();

    const resolveFromBase = (input: unknown): string | undefined => {
      const target = valueAsString(input);
      if (!target) {
        return undefined;
      }
      return path.resolve(baseRoot, target);
    };

    if (payload.command === "parse") {
      return resolveFromBase(payload.options.outdir) ?? this.viewerState.root ?? baseRoot;
    }

    if (payload.command === "chain") {
      const parsedRoot = resolveFromBase(payload.options.parsedRoot);
      if (parsedRoot) {
        return parsedRoot;
      }
      const outdir = resolveFromBase(payload.options.outdir);
      if (outdir) {
        return path.join(outdir, "output");
      }
    }

    return this.viewerState.root ?? baseRoot;
  }

  private setViewerError(code: ViewerErrorCode, detail?: string): void {
    this.viewerState = {
      ...this.viewerState,
      file: undefined,
      selectedPath: undefined,
      error: {
        code,
        detail,
      },
    };
    this.postViewerState();
  }

  private async handleRefreshFiles(
    payload?: RefreshFilesMessage["payload"]
  ): Promise<void> {
    this.syncWorkspaceRoot();
    const requestedRoot = valueAsString(payload?.root);
    const root = requestedRoot ?? this.viewerState.root ?? this.workspaceRoot;

    if (!root) {
      this.setViewerError("workspaceRequired");
      return;
    }

    const resolvedRoot = path.resolve(root);
    const validRoot = await isDirectory(resolvedRoot);
    if (!validRoot) {
      this.setViewerError("rootInvalid");
      return;
    }

    try {
      const files = await collectParsedJsonlFiles(resolvedRoot);
      const entries = files.map((filePath) => {
        const display = path.relative(resolvedRoot, filePath) || filePath;
        return {
          path: filePath,
          name: path.basename(path.dirname(filePath)),
          display,
        };
      });

      const selectedPath = this.viewerState.selectedPath;
      const selectedStillExists =
        typeof selectedPath === "string" &&
        entries.some((entry) => entry.path === selectedPath);

      this.viewerState = {
        ...this.viewerState,
        root: resolvedRoot,
        files: entries,
        selectedPath: selectedStillExists ? selectedPath : undefined,
        file: selectedStillExists ? this.viewerState.file : undefined,
        error: undefined,
      };
      this.postViewerState();
    } catch (error) {
      const detail = error instanceof Error ? error.message : undefined;
      this.setViewerError("listFailed", detail);
    }
  }

  private async handleViewerOpen(
    payload: OpenViewerFileMessage["payload"]
  ): Promise<void> {
    this.syncWorkspaceRoot();
    const root = this.viewerState.root ?? this.workspaceRoot;
    if (!root) {
      this.setViewerError("workspaceRequired");
      return;
    }

    if (!payload?.path) {
      this.setViewerError("noFile");
      return;
    }

    const resolved = path.resolve(payload.path);
    if (!isWithinRoot(root, resolved)) {
      this.setViewerError("outsideWorkspace");
      return;
    }

    try {
      const file = await readParsedJsonl(resolved);
      const viewerFile: ViewerFileData = {
        ...file,
        display: path.relative(root, resolved) || resolved,
      };

      this.viewerState = {
        ...this.viewerState,
        selectedPath: resolved,
        file: viewerFile,
        error: undefined,
      };
      this.postViewerState();
    } catch (error) {
      const detail = error instanceof Error ? error.message : undefined;
      this.setViewerError("readFailed", detail);
    }
  }

  private async postConfig(type: "config" | "config-changed"): Promise<void> {
    const config = resolveViewerConfig();
    const i18n = loadTranslations(this.extensionUri.fsPath, config.language);
    this.postMessage({
      type,
      config,
      i18n,
    });
  }

  private getHtmlForWebview(): string {
    const webview = this.panel.webview;
    const nonce = getNonce();
    const mediaRoot = vscode.Uri.file(
      resolveRuntimeMediaRoot(this.extensionUri.fsPath)
    );
    const stylesUri = webview.asWebviewUri(
      vscode.Uri.joinPath(mediaRoot, "styles.css")
    );
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(mediaRoot, "main.js")
    );
    const vendorScripts = buildVendorScriptTags(
      webview,
      nonce,
      this.extensionUri.fsPath
    );
    const templatePath = path.join(mediaRoot.fsPath, "index.html");
    const html = fs.readFileSync(templatePath, "utf8");

    return html
      .replace(/{{cspSource}}/g, webview.cspSource)
      .replace(/{{nonce}}/g, nonce)
      .replace(/{{stylesUri}}/g, stylesUri.toString())
      .replace(/{{vendorScripts}}/g, vendorScripts)
      .replace(/{{scriptUri}}/g, scriptUri.toString());
  }
}

export const __panelTestApi = {
  setRuntime(overrides: Partial<PanelRuntime> = {}): void {
    panelRuntime = {
      ...defaultPanelRuntime,
      ...overrides,
    };
  },
  resetRuntime(): void {
    panelRuntime = { ...defaultPanelRuntime };
  },
  async dispatchMessage(
    panel: LogParserPanel,
    message: WebviewToExtensionMessage
  ): Promise<void> {
    await (panel as unknown as { handleMessage: (message: WebviewToExtensionMessage) => Promise<void> }).handleMessage(message);
  },
};

interface RunHistoryEntry {
  id: string;
  ts: number;
  payload: CliRunPayload;
  success: boolean;
  exitCode?: number;
  commandLine?: string;
  errorWhat?: string;
}

const RUN_PRESET_OPTION_KEYS: Record<
  CliRunPayload["command"],
  readonly string[]
> = {
  parse: [
    "provider",
    "input",
    "outdir",
    "dryRun",
    "failFast",
    "validateSchema",
  ],
  export: [
    "input",
    "out",
    "timezone",
    "formatting",
    "split",
    "splitSoftOverflow",
    "splitHard",
    "splitPreview",
    "tinyTailThreshold",
  ],
  chain: [
    "provider",
    "input",
    "outdir",
    "timezone",
    "formatting",
    "split",
    "splitSoftOverflow",
    "splitHard",
    "splitPreview",
    "tinyTailThreshold",
    "exportOutdir",
    "parsedRoot",
    "dryRun",
    "failFast",
    "validateSchema",
  ],
  analyze: [
    "analyzeCommand",
    "input",
    "json",
    "out",
    "perThread",
    "top",
    "sort",
    "includeRoleBreakdown",
    "bucket",
    "model",
    "encoding",
    "skipExisting",
    "dryRun",
  ],
};

const createRunPreset = (
  payload: CliRunPayload
): ApplyRunPresetMessage["preset"] => {
  const values: ApplyRunPresetMessage["preset"]["values"] = {};

  for (const key of RUN_PRESET_OPTION_KEYS[payload.command]) {
    const value = payload.options[key];
    if (typeof value === "string" || typeof value === "boolean") {
      values[key] = value;
    }
  }

  return {
    command: payload.command,
    values,
  };
};

const basenameOrValue = (value: unknown): string | undefined => {
  const target = valueAsString(value);
  if (!target) {
    return undefined;
  }
  return path.basename(target) || target;
};

const buildRunHistoryLabel = (payload: CliRunPayload): string => {
  if (payload.command === "analyze") {
    const analyzeCommand = valueAsString(payload.options.analyzeCommand) ?? "analyze";
    const input = basenameOrValue(payload.options.input);
    return input ? `analyze ${analyzeCommand} · ${input}` : `analyze ${analyzeCommand}`;
  }

  if (payload.command === "parse" || payload.command === "chain") {
    const provider = valueAsString(payload.options.provider);
    const input = basenameOrValue(payload.options.input);
    const parts: string[] = [payload.command];
    if (provider) {
      parts.push(provider);
    }
    if (input) {
      parts.push(input);
    }
    return parts.join(" · ");
  }

  const input = basenameOrValue(payload.options.input);
  return input ? `${payload.command} · ${input}` : payload.command;
};

const buildRunHistoryDetail = (entry: RunHistoryEntry): string | undefined => {
  if (!entry.success) {
    return entry.errorWhat ?? "Last run did not finish successfully.";
  }

  if (typeof entry.exitCode === "number") {
    return `Finished with exit code ${entry.exitCode}.`;
  }

  return entry.commandLine;
};

const valueAsString = (value: unknown): string | undefined => {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
};

const getNonce = (): string => {
  let text = "";
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 16; i += 1) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
};

const IGNORED_DIRS = new Set([
  "node_modules",
  ".git",
  ".venv",
  "__pycache__",
  "dist",
  "out",
]);

const isWithinRoot = (root: string, target: string): boolean => {
  const resolvedRoot = path.resolve(root);
  const resolvedTarget = path.resolve(target);
  return (
    resolvedTarget === resolvedRoot ||
    resolvedTarget.startsWith(`${resolvedRoot}${path.sep}`)
  );
};

const isDirectory = async (target: string): Promise<boolean> => {
  try {
    const stats = await fs.promises.stat(target);
    return stats.isDirectory();
  } catch (error) {
    return false;
  }
};

const resolveViewerConfig = (): ViewerConfig => {
  const config = vscode.workspace.getConfiguration("llmLogparser");
  const languageSetting = config.get<string>("viewer.language") ?? "auto";
  const language = resolveLanguage(languageSetting);

  const timezone = resolveEnum(config.get<string>("viewer.timezone"), ["local", "utc"], "local");
  const timestampFormat = resolveEnum(
    config.get<string>("viewer.timestampFormat"),
    ["relative", "absolute"],
    "absolute"
  );
  const wrap = (config.get<string>("viewer.wrap") ?? "on") === "on";
  const showSystem = (config.get<string>("viewer.showSystem") ?? "on") === "on";
  const showToolCalls = (config.get<string>("viewer.showToolCalls") ?? "on") === "on";
  const compactMode = (config.get<string>("viewer.compactMode") ?? "off") === "on";
  const codeTheme = resolveEnum(
    config.get<string>("viewer.codeTheme"),
    ["auto", "light", "dark"],
    "auto"
  );
  const maxMessagesRaw = config.get<number>("viewer.maxMessagesPerThread");
  const maxMessages =
    typeof maxMessagesRaw === "number" && Number.isFinite(maxMessagesRaw)
      ? Math.max(0, Math.floor(maxMessagesRaw))
      : 2000;

  const caseSensitive = Boolean(config.get<boolean>("viewer.search.caseSensitive"));
  const useRegex = Boolean(config.get<boolean>("viewer.search.useRegex"));

  return {
    language,
    timezone,
    timestampFormat,
    wrap,
    showSystem,
    showToolCalls,
    compactMode,
    codeTheme,
    maxMessagesPerThread: maxMessages,
    search: {
      caseSensitive,
      useRegex,
    },
  };
};

const resolveLanguage = (setting: string): "en" | "ja" => {
  if (setting === "en" || setting === "ja") {
    return setting;
  }
  const envLanguage = vscode.env.language.toLowerCase();
  return envLanguage.startsWith("ja") ? "ja" : "en";
};

const resolveEnum = <T extends string>(
  value: string | undefined,
  allowed: readonly T[],
  fallback: T
): T => {
  if (!value) {
    return fallback;
  }
  return allowed.includes(value as T) ? (value as T) : fallback;
};

const readTranslationsFile = (basePath: string, language: string): Record<string, string> => {
  const target = path.join(basePath, `${language}.json`);
  const raw = fs.readFileSync(target, "utf8");
  return JSON.parse(raw) as Record<string, string>;
};

const loadTranslations = (root: string, language: string): Record<string, string> => {
  const basePath = path.join(resolveRuntimeMediaRoot(root), "i18n");
  try {
    const fallback = readTranslationsFile(basePath, "en");
    if (language === "en") {
      return fallback;
    }

    try {
      const localized = readTranslationsFile(basePath, language);
      return {
        ...fallback,
        ...localized,
      };
    } catch (error) {
      return fallback;
    }
  } catch (error) {
    return {};
  }
};

const collectParsedJsonlFiles = async (root: string): Promise<string[]> => {
  const results: string[] = [];
  const stack: string[] = [root];

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) {
      continue;
    }

    let entries: fs.Dirent[] = [];
    try {
      entries = await fs.promises.readdir(current, { withFileTypes: true });
    } catch (error) {
      continue;
    }

    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (!IGNORED_DIRS.has(entry.name)) {
          stack.push(fullPath);
        }
        continue;
      }
      if (entry.isFile() && entry.name === "parsed.jsonl") {
        results.push(fullPath);
      }
    }
  }

  return results.sort();
};

const resolveRuntimeMediaRoot = (root: string): string =>
  resolveExistingPath(root, [
    ["dist", "ui", "media"],
    ["src", "ui", "media"],
  ]);

const resolveRuntimeVendorRoot = (root: string): string | undefined =>
  resolveExistingPath(root, [["dist", "ui", "vendor"]], false);

const resolveExistingPath = (
  root: string,
  candidates: string[][],
  required = true
): string => {
  for (const segments of candidates) {
    const candidate = path.join(root, ...segments);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  const fallback = path.join(root, ...candidates[0]);
  if (required) {
    return fallback;
  }
  return "";
};

const getWebviewResourceRoots = (extensionUri: vscode.Uri): vscode.Uri[] => {
  const roots = [vscode.Uri.file(resolveRuntimeMediaRoot(extensionUri.fsPath))];
  const vendorRoot = resolveRuntimeVendorRoot(extensionUri.fsPath);
  if (vendorRoot) {
    roots.push(vscode.Uri.file(vendorRoot));
  }
  return roots;
};

const buildVendorScriptTags = (
  webview: vscode.Webview,
  nonce: string,
  root: string
): string => {
  const vendorRoot = resolveRuntimeVendorRoot(root);
  if (!vendorRoot) {
    return "";
  }

  const vendorFiles = ["marked.umd.js", "purify.min.js"];
  return vendorFiles
    .filter((fileName) => fs.existsSync(path.join(vendorRoot, fileName)))
    .map((fileName) => {
      const uri = webview.asWebviewUri(vscode.Uri.file(path.join(vendorRoot, fileName)));
      return `<script nonce="${nonce}" src="${uri.toString()}" defer></script>`;
    })
    .join("\n    ");
};

const readParsedJsonl = async (filePath: string): Promise<ViewerFileData> => {
  const stream = fs.createReadStream(filePath, { encoding: "utf8" });
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });

  let meta: ViewerFileData["meta"] | undefined;
  const messages: ViewerMessage[] = [];

  for await (const line of rl) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    let row: Record<string, unknown> | undefined;
    try {
      row = JSON.parse(trimmed) as Record<string, unknown>;
    } catch (error) {
      continue;
    }

    const recordType = row.record_type;
    if (recordType === "thread" && !meta) {
      meta = {
        provider_id: typeof row.provider_id === "string" ? row.provider_id : undefined,
        conversation_id:
          typeof row.conversation_id === "string" ? row.conversation_id : undefined,
        message_count:
          typeof row.message_count === "number" ? row.message_count : undefined,
      };
      continue;
    }
    if (recordType === "message") {
      const rowMeta =
        row.meta && typeof row.meta === "object"
          ? (row.meta as Record<string, unknown>)
          : undefined;
      messages.push({
        role: typeof row.role === "string" ? row.role : "",
        ts: typeof row.ts === "number" ? row.ts : undefined,
        text: typeof row.text === "string" ? row.text : "",
        model:
          typeof row.model === "string"
            ? row.model
            : typeof rowMeta?.model === "string"
              ? rowMeta.model
              : undefined,
      });
    }
  }

  return { path: filePath, meta, messages };
};
