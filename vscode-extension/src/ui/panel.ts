import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import * as readline from "readline";
import { runCli, type CliCommand, type RunCliRequest } from "../backend/python";

type RunPayload = {
  command: CliCommand;
  options: Record<string, string | boolean | undefined>;
};

type PickPayload = {
  targetId: string;
  kind: "file" | "folder";
};

type ViewerListEntry = {
  path: string;
  name: string;
  display: string;
};

type ViewerListPayload = {
  root?: string;
};

type ViewerOpenPayload = {
  path: string;
};

type ViewerMessage = {
  role: string;
  ts?: number;
  text: string;
};

type ViewerFilePayload = {
  path: string;
  meta?: {
    provider_id?: string;
    conversation_id?: string;
    message_count?: number;
  };
  messages: ViewerMessage[];
};

type ViewerConfig = {
  language: "en" | "ja";
  timezone: "local" | "utc";
  timestampFormat: "relative" | "absolute";
  wrap: boolean;
  showSystem: boolean;
  showToolCalls: boolean;
  compactMode: boolean;
  codeTheme: "auto" | "light" | "dark";
  maxMessagesPerThread: number;
  search: {
    caseSensitive: boolean;
    useRegex: boolean;
  };
};

export class LogParserPanel {
  public static currentPanel: LogParserPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly extensionUri: vscode.Uri;
  private disposables: vscode.Disposable[] = [];
  private viewerRoot?: string;

  private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
    this.panel = panel;
    this.extensionUri = extensionUri;

    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      (message) => this.handleMessage(message),
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
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    this.panel.webview.postMessage({
      type: "init",
      workspaceRoot,
    });
    void this.postConfig("config");
  }

  private async handleMessage(message: { type: string; payload?: unknown }) {
    switch (message.type) {
      case "pick":
        await this.handlePick(message.payload as PickPayload);
        return;
      case "run":
        await this.handleRun(message.payload as RunPayload);
        return;
      case "viewer-list":
        await this.handleViewerList(message.payload as ViewerListPayload);
        return;
      case "viewer-open":
        await this.handleViewerOpen(message.payload as ViewerOpenPayload);
        return;
      case "clear-log":
        this.panel.webview.postMessage({ type: "clear-log" });
        return;
      default:
        return;
    }
  }

  private async handlePick(payload: PickPayload): Promise<void> {
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

    this.panel.webview.postMessage({
      type: "pick-result",
      targetId: payload.targetId,
      value: result[0].fsPath,
    });
  }

  private async handleRun(payload: RunPayload): Promise<void> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!workspaceRoot) {
      vscode.window.showErrorMessage("Workspace folder is required.");
      return;
    }

    const missing = validatePayload(payload);
    if (missing.length > 0) {
      this.panel.webview.postMessage({
        type: "run-error",
        fields: missing,
      });
      return;
    }

    const args = buildArgs(payload);
    const config = vscode.workspace.getConfiguration("llmLogparser");
    const pythonPath = config.get<string>("pythonPath") ?? "python3";
    const cliCommand = config.get<string>("cliCommand") ?? "";

    const runRequest: RunCliRequest = {
      command: payload.command,
      args,
    };

    const commandLine = buildCommandLine(runRequest, pythonPath, cliCommand);
    this.panel.webview.postMessage({ type: "busy", value: true });
    this.panel.webview.postMessage({ type: "log", value: `> ${commandLine}\n` });

    try {
      const exitCode = await runCli(runRequest, {
        cwd: workspaceRoot,
        pythonPath,
        cliCommand,
        onStdout: (chunk) =>
          this.panel.webview.postMessage({ type: "log", value: chunk }),
        onStderr: (chunk) =>
          this.panel.webview.postMessage({ type: "log", value: chunk }),
      });

      this.panel.webview.postMessage({
        type: "run-finished",
        exitCode,
      });
    } catch (error) {
      this.panel.webview.postMessage({
        type: "run-failed",
        message: error instanceof Error ? error.message : undefined,
      });
    } finally {
      this.panel.webview.postMessage({ type: "busy", value: false });
    }
  }

  private async handleViewerList(payload?: ViewerListPayload): Promise<void> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const requestedRoot = valueAsString(payload?.root);
    const root = requestedRoot ?? workspaceRoot;

    if (!root) {
      this.panel.webview.postMessage({
        type: "viewer-error",
        code: "workspaceRequired",
      });
      return;
    }

    const resolvedRoot = path.resolve(root);
    const validRoot = await isDirectory(resolvedRoot);
    if (!validRoot) {
      this.panel.webview.postMessage({
        type: "viewer-error",
        code: "rootInvalid",
      });
      return;
    }

    this.viewerRoot = resolvedRoot;

    try {
      const files = await collectParsedJsonlFiles(resolvedRoot);
      const entries: ViewerListEntry[] = files.map((filePath) => {
        const display = path.relative(resolvedRoot, filePath) || filePath;
        return {
          path: filePath,
          name: path.basename(path.dirname(filePath)),
          display,
        };
      });
      this.panel.webview.postMessage({ type: "viewer-files", files: entries });
    } catch (error) {
      const detail = error instanceof Error ? error.message : undefined;
      this.panel.webview.postMessage({
        type: "viewer-error",
        code: "listFailed",
        detail,
      });
    }
  }

  private async handleViewerOpen(payload: ViewerOpenPayload): Promise<void> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const root = this.viewerRoot ?? workspaceRoot;
    if (!root) {
      this.panel.webview.postMessage({
        type: "viewer-error",
        code: "workspaceRequired",
      });
      return;
    }

    if (!payload?.path) {
      this.panel.webview.postMessage({
        type: "viewer-error",
        code: "noFile",
      });
      return;
    }

    const resolved = path.resolve(payload.path);
    if (!isWithinRoot(root, resolved)) {
      this.panel.webview.postMessage({
        type: "viewer-error",
        code: "outsideWorkspace",
      });
      return;
    }

    try {
      const payloadData = await readParsedJsonl(resolved);
      this.panel.webview.postMessage({
        type: "viewer-file",
        display: path.relative(root, resolved) || resolved,
        ...payloadData,
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : undefined;
      this.panel.webview.postMessage({
        type: "viewer-error",
        code: "readFailed",
        detail,
      });
    }
  }

  private async postConfig(type: "config" | "config-changed"): Promise<void> {
    const config = resolveViewerConfig();
    const i18n = loadTranslations(this.extensionUri.fsPath, config.language);
    this.panel.webview.postMessage({
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

const valueAsBoolean = (value: unknown): boolean => Boolean(value);

const validatePayload = (payload: RunPayload): string[] => {
  const missing: string[] = [];
  const opts = payload.options;
  if (payload.command === "parse") {
    if (!valueAsString(opts.provider)) missing.push("provider");
    if (!valueAsString(opts.input)) missing.push("input");
  } else if (payload.command === "export") {
    if (!valueAsString(opts.input)) missing.push("input");
  } else if (payload.command === "chain") {
    if (!valueAsString(opts.provider)) missing.push("provider");
    if (!valueAsString(opts.input)) missing.push("input");
  }
  return missing;
};

const buildArgs = (payload: RunPayload): string[] => {
  const args: string[] = [];
  const opts = payload.options;

  const add = (flag: string, value?: string) => {
    if (value) {
      args.push(flag, value);
    }
  };
  const addFlag = (flag: string, enabled: boolean) => {
    if (enabled) {
      args.push(flag);
    }
  };

  if (payload.command === "parse") {
    add("--provider", valueAsString(opts.provider));
    add("--input", valueAsString(opts.input));
    add("--outdir", valueAsString(opts.outdir));
    addFlag("--dry-run", valueAsBoolean(opts.dryRun));
    addFlag("--fail-fast", valueAsBoolean(opts.failFast));
    addFlag("--validate-schema", valueAsBoolean(opts.validateSchema));
  } else if (payload.command === "export") {
    add("--input", valueAsString(opts.input));
    add("--out", valueAsString(opts.out));
    add("--timezone", valueAsString(opts.timezone));
    add("--formatting", valueAsString(opts.formatting));
    add("--split", valueAsString(opts.split));
    add("--split-soft-overflow", valueAsString(opts.splitSoftOverflow));
    addFlag("--split-hard", valueAsBoolean(opts.splitHard));
    addFlag("--split-preview", valueAsBoolean(opts.splitPreview));
    add("--tiny-tail-threshold", valueAsString(opts.tinyTailThreshold));
  } else if (payload.command === "chain") {
    add("--provider", valueAsString(opts.provider));
    add("--input", valueAsString(opts.input));
    add("--outdir", valueAsString(opts.outdir));
    add("--timezone", valueAsString(opts.timezone));
    add("--formatting", valueAsString(opts.formatting));
    add("--split", valueAsString(opts.split));
    add("--split-soft-overflow", valueAsString(opts.splitSoftOverflow));
    addFlag("--split-hard", valueAsBoolean(opts.splitHard));
    addFlag("--split-preview", valueAsBoolean(opts.splitPreview));
    add("--tiny-tail-threshold", valueAsString(opts.tinyTailThreshold));
    add("--export-outdir", valueAsString(opts.exportOutdir));
    add("--parsed-root", valueAsString(opts.parsedRoot));
    addFlag("--dry-run", valueAsBoolean(opts.dryRun));
    addFlag("--fail-fast", valueAsBoolean(opts.failFast));
    addFlag("--validate-schema", valueAsBoolean(opts.validateSchema));
  }

  return args;
};

const buildCommandLine = (
  request: RunCliRequest,
  pythonPath: string,
  cliCommand: string
): string => {
  const args = [request.command, ...request.args];
  if (cliCommand && cliCommand.trim().length > 0) {
    return `${cliCommand} ${args.join(" ")}`;
  }
  return `${pythonPath} -m llm_logparser.cli ${args.join(" ")}`;
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

const loadTranslations = (root: string, language: string): Record<string, string> => {
  const basePath = path.join(root, "src", "ui", "media", "i18n");
  const primary = path.join(basePath, `${language}.json`);
  try {
    const raw = fs.readFileSync(primary, "utf8");
    return JSON.parse(raw) as Record<string, string>;
  } catch (error) {
    if (language !== "en") {
      const fallback = path.join(basePath, "en.json");
      try {
        const raw = fs.readFileSync(fallback, "utf8");
        return JSON.parse(raw) as Record<string, string>;
      } catch (fallbackError) {
        return {};
      }
    }
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

const readParsedJsonl = async (filePath: string): Promise<ViewerFilePayload> => {
  const stream = fs.createReadStream(filePath, { encoding: "utf8" });
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });

  let meta: ViewerFilePayload["meta"] | undefined;
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
