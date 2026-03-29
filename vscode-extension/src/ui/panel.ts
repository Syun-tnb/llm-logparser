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
  ExtensionToWebviewMessage,
  OpenViewerFileMessage,
  PickMessage,
  RefreshFilesMessage,
  RunState,
  ValidationStateMessage,
  ViewerConfig,
  ViewerErrorCode,
  ViewerFileData,
  ViewerMessage,
  ViewerState,
  ViewerStateMessage,
  WebviewToExtensionMessage,
} from "./protocol";

export class LogParserPanel {
  public static currentPanel: LogParserPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly extensionUri: vscode.Uri;
  private disposables: vscode.Disposable[] = [];
  private workspaceRoot?: string;
  private runState: RunState;
  private viewerState: ViewerState;

  private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
    this.panel = panel;
    this.extensionUri = extensionUri;
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

  public static createOrShow(extensionUri: vscode.Uri): void {
    const column = vscode.window.activeTextEditor
      ? vscode.window.activeTextEditor.viewColumn
      : undefined;

    if (LogParserPanel.currentPanel) {
      LogParserPanel.currentPanel.panel.reveal(column);
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      "llmLogparserPanel",
      "LLM Logparser",
      column ?? vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [
          vscode.Uri.joinPath(extensionUri, "src", "ui", "media"),
        ],
      }
    );

    LogParserPanel.currentPanel = new LogParserPanel(panel, extensionUri);
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

  private postInit(): void {
    this.syncWorkspaceRoot();
    this.postMessage({
      type: "init",
      workspaceRoot: this.workspaceRoot,
      runState: this.runState,
      viewerState: this.viewerState,
    });
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
    const workspaceRoot = this.workspaceRoot ?? "";
    const config = vscode.workspace.getConfiguration("llmLogparser");
    const pythonPath = config.get<string>("pythonPath") ?? "python3";
    const cliCommand = config.get<string>("cliCommand") ?? "";
    const invalidFields = getInvalidCliFields(payload);

    this.postValidationState(payload.command, invalidFields);
    if (invalidFields.length > 0) {
      const missing = invalidFields.join(", ");
      const uiError = toCliUiError(
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
      return;
    }

    try {
      const runRequest = createRunCliRequest(payload);
      const commandLine = await formatCliCommandLine(runRequest, {
        cwd: workspaceRoot,
        pythonPath,
        cliCommand,
      });

      this.runState = {
        busy: true,
      };
      this.setBusy(true);
      this.postMessage({ type: "log", value: `> ${commandLine}\n` });

      const exitCode = await runCli(runRequest, {
        cwd: workspaceRoot,
        pythonPath,
        cliCommand,
        onStdout: (chunk) =>
          this.postMessage({ type: "log", value: chunk }),
        onStderr: (chunk) =>
          this.postMessage({ type: "log", value: chunk }),
      });

      this.runState = {
        busy: false,
        lastExitCode: exitCode,
      };
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
      const uiError = toCliUiError(error);
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
    } finally {
      this.setBusy(false);
    }
  }

  private getViewerRootForCommand(payload: CliRunPayload): string | undefined {
    const workspaceRoot = this.workspaceRoot;
    if (!workspaceRoot) {
      return undefined;
    }

    const resolveFromWorkspace = (input: unknown): string | undefined => {
      const target = valueAsString(input);
      if (!target) {
        return undefined;
      }
      return path.resolve(workspaceRoot, target);
    };

    if (payload.command === "parse") {
      return resolveFromWorkspace(payload.options.outdir) ?? this.viewerState.root ?? workspaceRoot;
    }

    if (payload.command === "chain") {
      const parsedRoot = resolveFromWorkspace(payload.options.parsedRoot);
      if (parsedRoot) {
        return parsedRoot;
      }
      const outdir = resolveFromWorkspace(payload.options.outdir);
      if (outdir) {
        return path.join(outdir, "output");
      }
    }

    return this.viewerState.root ?? workspaceRoot;
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
    const mediaRoot = vscode.Uri.joinPath(
      this.extensionUri,
      "src",
      "ui",
      "media"
    );
    const stylesUri = webview.asWebviewUri(
      vscode.Uri.joinPath(mediaRoot, "styles.css")
    );
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(mediaRoot, "main.js")
    );
    const templatePath = path.join(
      this.extensionUri.fsPath,
      "src",
      "ui",
      "media",
      "index.html"
    );
    const html = fs.readFileSync(templatePath, "utf8");

    return html
      .replace(/{{cspSource}}/g, webview.cspSource)
      .replace(/{{nonce}}/g, nonce)
      .replace(/{{stylesUri}}/g, stylesUri.toString())
      .replace(/{{scriptUri}}/g, scriptUri.toString());
  }
}

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
  const basePath = path.join(root, "src", "ui", "media", "i18n");
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
      messages.push({
        role: typeof row.role === "string" ? row.role : "",
        ts: typeof row.ts === "number" ? row.ts : undefined,
        text: typeof row.text === "string" ? row.text : "",
      });
    }
  }

  return { path: filePath, meta, messages };
};
