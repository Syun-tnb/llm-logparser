import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { runCli, type CliCommand, type RunCliRequest } from "../backend/python";

type RunPayload = {
  command: CliCommand;
  options: Record<string, string | boolean | undefined>;
};

type PickPayload = {
  targetId: string;
  kind: "file" | "folder";
};

export class LogParserPanel {
  public static currentPanel: LogParserPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly extensionUri: vscode.Uri;
  private disposables: vscode.Disposable[] = [];

  private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
    this.panel = panel;
    this.extensionUri = extensionUri;

    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      (message) => this.handleMessage(message),
      null,
      this.disposables
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
  }

  private async handleMessage(message: { type: string; payload?: unknown }) {
    switch (message.type) {
      case "pick":
        await this.handlePick(message.payload as PickPayload);
        return;
      case "run":
        await this.handleRun(message.payload as RunPayload);
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
        message: `Missing required fields: ${missing.join(", ")}`,
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
        type: "log",
        value: `\nProcess finished with exit code ${exitCode}.\n`,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unknown error occurred.";
      this.panel.webview.postMessage({
        type: "log",
        value: `\nFailed to run command: ${message}\n`,
      });
    } finally {
      this.panel.webview.postMessage({ type: "busy", value: false });
    }
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
