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

    return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta
      http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src ${webview.cspSource} 'nonce-${nonce}'; script-src 'nonce-${nonce}';"
    />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>LLM Logparser</title>
    <style nonce="${nonce}">
      :root {
        color-scheme: light;
        --bg: #f6f1e6;
        --bg-alt: #fef9f0;
        --ink: #1b1a17;
        --muted: #5b5a55;
        --accent: #0b6e4f;
        --accent-strong: #0e8b63;
        --border: #d3c7b5;
        --shadow: rgba(27, 26, 23, 0.1);
        --card: rgba(255, 255, 255, 0.72);
        --warn: #b86119;
      }

      * {
        box-sizing: border-box;
      }

      body {
        margin: 0;
        font-family: "Fira Sans", "Segoe UI", "Helvetica Neue", sans-serif;
        color: var(--ink);
        background: radial-gradient(circle at top, #fffdf7 0%, var(--bg) 55%, #efe5d5 100%);
        min-height: 100vh;
      }

      .page {
        padding: 24px;
        animation: fadeIn 0.35s ease-out;
      }

      .hero {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-bottom: 24px;
      }

      .hero h1 {
        margin: 0;
        font-size: 28px;
        letter-spacing: 0.4px;
      }

      .hero p {
        margin: 0;
        color: var(--muted);
        max-width: 720px;
      }

      .workspace {
        font-size: 12px;
        color: var(--muted);
        letter-spacing: 0.2px;
      }

      .layout {
        display: grid;
        grid-template-columns: minmax(260px, 1fr) minmax(280px, 360px);
        gap: 20px;
      }

      .card {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 18px;
        box-shadow: 0 12px 30px var(--shadow);
        backdrop-filter: blur(6px);
      }

      .card h2 {
        margin: 0 0 12px;
        font-size: 18px;
      }

      .field {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-bottom: 12px;
      }

      label {
        font-size: 13px;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }

      input,
      select,
      textarea {
        padding: 10px 12px;
        border-radius: 10px;
        border: 1px solid var(--border);
        background: var(--bg-alt);
        font-size: 14px;
        color: var(--ink);
      }

      input:focus,
      select:focus,
      textarea:focus {
        outline: 2px solid rgba(11, 110, 79, 0.35);
        border-color: var(--accent);
      }

      .row {
        display: flex;
        gap: 8px;
      }

      .row > * {
        flex: 1;
      }

      .inline {
        display: flex;
        gap: 10px;
        align-items: center;
      }

      .inline input[type="checkbox"] {
        width: 16px;
        height: 16px;
      }

      button {
        cursor: pointer;
        border: none;
        padding: 10px 14px;
        border-radius: 12px;
        font-size: 14px;
        background: var(--accent);
        color: #fff;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }

      button.secondary {
        background: #f2e8d8;
        color: var(--ink);
        border: 1px solid var(--border);
      }

      button:disabled {
        cursor: not-allowed;
        opacity: 0.6;
      }

      button:hover:not(:disabled) {
        transform: translateY(-1px);
        box-shadow: 0 8px 18px rgba(11, 110, 79, 0.2);
      }

      .actions {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
      }

      .log {
        background: #12120f;
        color: #d8d2c7;
        border-radius: 12px;
        padding: 12px;
        font-family: "JetBrains Mono", "SFMono-Regular", "Menlo", monospace;
        font-size: 12px;
        min-height: 200px;
        max-height: 360px;
        overflow-y: auto;
        white-space: pre-wrap;
      }

      .warning {
        color: var(--warn);
        font-size: 12px;
      }

      .section {
        padding: 12px;
        border-radius: 12px;
        border: 1px dashed rgba(91, 90, 85, 0.35);
        margin-bottom: 16px;
        animation: reveal 0.35s ease-out;
      }

      .hidden {
        display: none;
      }

      @media (max-width: 980px) {
        .layout {
          grid-template-columns: 1fr;
        }
      }

      @keyframes fadeIn {
        from {
          opacity: 0;
          transform: translateY(10px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      @keyframes reveal {
        from {
          opacity: 0;
          transform: translateY(8px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
    </style>
  </head>
  <body>
    <div class="page">
      <div class="hero">
        <h1>LLM Logparser</h1>
        <p>Drive the CLI from a focused GUI. Choose a command, pick inputs, and run parse/export pipelines.</p>
        <div class="workspace" id="workspaceRoot">Workspace: -</div>
      </div>

      <div class="layout">
        <div class="card">
          <h2>Command Setup</h2>
          <div class="field">
            <label for="command">Command</label>
            <select id="command">
              <option value="parse">parse</option>
              <option value="export">export</option>
              <option value="chain">chain</option>
            </select>
          </div>

          <div id="section-parse" class="section">
            <div class="field">
              <label for="parse-provider">Provider</label>
              <input id="parse-provider" type="text" placeholder="openai" value="openai" />
            </div>
            <div class="field">
              <label for="parse-input">Input JSON/JSONL</label>
              <div class="row">
                <input id="parse-input" type="text" placeholder="path/to/export.json" />
                <button class="secondary" data-pick="file" data-target="parse-input">Browse</button>
              </div>
            </div>
            <div class="field">
              <label for="parse-outdir">Output Directory</label>
              <div class="row">
                <input id="parse-outdir" type="text" placeholder="artifacts" value="artifacts" />
                <button class="secondary" data-pick="folder" data-target="parse-outdir">Browse</button>
              </div>
            </div>
            <div class="inline">
              <input id="parse-dry-run" type="checkbox" />
              <label for="parse-dry-run">Dry Run</label>
            </div>
            <div class="inline">
              <input id="parse-fail-fast" type="checkbox" />
              <label for="parse-fail-fast">Fail Fast</label>
            </div>
            <div class="inline">
              <input id="parse-validate-schema" type="checkbox" />
              <label for="parse-validate-schema">Validate Schema</label>
            </div>
          </div>

          <div id="section-export" class="section hidden">
            <div class="field">
              <label for="export-input">Input parsed.jsonl</label>
              <div class="row">
                <input id="export-input" type="text" placeholder="thread-*/parsed.jsonl" />
                <button class="secondary" data-pick="file" data-target="export-input">Browse</button>
              </div>
            </div>
            <div class="field">
              <label for="export-out">Output Markdown (optional)</label>
              <div class="row">
                <input id="export-out" type="text" placeholder="thread-123.md" />
                <button class="secondary" data-pick="file" data-target="export-out">Browse</button>
              </div>
            </div>
            <div class="row">
              <div class="field">
                <label for="export-timezone">Timezone</label>
                <input id="export-timezone" type="text" value="UTC" />
              </div>
              <div class="field">
                <label for="export-formatting">Formatting</label>
                <select id="export-formatting">
                  <option value="light">light</option>
                  <option value="none">none</option>
                </select>
              </div>
            </div>
            <div class="field">
              <label for="export-split">Split (size= / count= / auto)</label>
              <input id="export-split" type="text" placeholder="size=4M" />
            </div>
            <div class="row">
              <div class="field">
                <label for="export-split-soft-overflow">Split Soft Overflow</label>
                <input id="export-split-soft-overflow" type="number" placeholder="0.2" />
              </div>
              <div class="field">
                <label for="export-tiny-tail-threshold">Tiny Tail Threshold</label>
                <input id="export-tiny-tail-threshold" type="number" placeholder="20" />
              </div>
            </div>
            <div class="inline">
              <input id="export-split-hard" type="checkbox" />
              <label for="export-split-hard">Split Hard</label>
            </div>
            <div class="inline">
              <input id="export-split-preview" type="checkbox" />
              <label for="export-split-preview">Split Preview</label>
            </div>
          </div>

          <div id="section-chain" class="section hidden">
            <div class="field">
              <label for="chain-provider">Provider</label>
              <input id="chain-provider" type="text" placeholder="openai" value="openai" />
            </div>
            <div class="field">
              <label for="chain-input">Input JSON/JSONL</label>
              <div class="row">
                <input id="chain-input" type="text" placeholder="path/to/export.json" />
                <button class="secondary" data-pick="file" data-target="chain-input">Browse</button>
              </div>
            </div>
            <div class="field">
              <label for="chain-outdir">Output Directory</label>
              <div class="row">
                <input id="chain-outdir" type="text" placeholder="artifacts" value="artifacts" />
                <button class="secondary" data-pick="folder" data-target="chain-outdir">Browse</button>
              </div>
            </div>
            <div class="row">
              <div class="field">
                <label for="chain-timezone">Timezone</label>
                <input id="chain-timezone" type="text" value="UTC" />
              </div>
              <div class="field">
                <label for="chain-formatting">Formatting</label>
                <select id="chain-formatting">
                  <option value="light">light</option>
                  <option value="none">none</option>
                </select>
              </div>
            </div>
            <div class="field">
              <label for="chain-split">Split (size= / count= / auto)</label>
              <input id="chain-split" type="text" placeholder="auto" />
            </div>
            <div class="row">
              <div class="field">
                <label for="chain-split-soft-overflow">Split Soft Overflow</label>
                <input id="chain-split-soft-overflow" type="number" placeholder="0.2" />
              </div>
              <div class="field">
                <label for="chain-tiny-tail-threshold">Tiny Tail Threshold</label>
                <input id="chain-tiny-tail-threshold" type="number" placeholder="20" />
              </div>
            </div>
            <div class="inline">
              <input id="chain-split-hard" type="checkbox" />
              <label for="chain-split-hard">Split Hard</label>
            </div>
            <div class="inline">
              <input id="chain-split-preview" type="checkbox" />
              <label for="chain-split-preview">Split Preview</label>
            </div>
            <div class="field">
              <label for="chain-export-outdir">Export Outdir (optional)</label>
              <div class="row">
                <input id="chain-export-outdir" type="text" placeholder="artifacts/export" />
                <button class="secondary" data-pick="folder" data-target="chain-export-outdir">Browse</button>
              </div>
            </div>
            <div class="field">
              <label for="chain-parsed-root">Parsed Root (optional)</label>
              <div class="row">
                <input id="chain-parsed-root" type="text" placeholder="artifacts/output/openai" />
                <button class="secondary" data-pick="folder" data-target="chain-parsed-root">Browse</button>
              </div>
            </div>
            <div class="inline">
              <input id="chain-dry-run" type="checkbox" />
              <label for="chain-dry-run">Dry Run</label>
            </div>
            <div class="inline">
              <input id="chain-fail-fast" type="checkbox" />
              <label for="chain-fail-fast">Fail Fast</label>
            </div>
            <div class="inline">
              <input id="chain-validate-schema" type="checkbox" />
              <label for="chain-validate-schema">Validate Schema</label>
            </div>
          </div>

          <div class="actions">
            <button id="run">Run Command</button>
            <button id="clear" class="secondary">Clear Log</button>
          </div>
          <p class="warning">Ensure Python dependencies are installed in this workspace before running.</p>
        </div>

        <div class="card">
          <h2>Command Output</h2>
          <div class="log" id="log"></div>
        </div>
      </div>
    </div>

    <script nonce="${nonce}">
      const vscode = acquireVsCodeApi();
      const logEl = document.getElementById("log");
      const commandSelect = document.getElementById("command");
      const sections = {
        parse: document.getElementById("section-parse"),
        export: document.getElementById("section-export"),
        chain: document.getElementById("section-chain"),
      };

      const showSection = (command) => {
        Object.entries(sections).forEach(([key, element]) => {
          if (key === command) {
            element.classList.remove("hidden");
          } else {
            element.classList.add("hidden");
          }
        });
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

      document.getElementById("run").addEventListener("click", () => {
        const payload = collectPayload(commandSelect.value);
        vscode.postMessage({ type: "run", payload });
      });

      document.getElementById("clear").addEventListener("click", () => {
        vscode.postMessage({ type: "clear-log" });
      });

      commandSelect.addEventListener("change", (event) => {
        showSection(event.target.value);
      });

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

      const valueOf = (id) => document.getElementById(id).value.trim();
      const checked = (id) => document.getElementById(id).checked;

      window.addEventListener("message", (event) => {
        const message = event.data;
        switch (message.type) {
          case "log":
            appendLog(message.value);
            return;
          case "clear-log":
            logEl.textContent = "";
            return;
          case "pick-result":
            const target = document.getElementById(message.targetId);
            if (target) {
              target.value = message.value;
            }
            return;
          case "busy":
            document.getElementById("run").disabled = message.value;
            return;
          case "run-error":
            appendLog("\\n[error] " + message.message + "\\n");
            return;
          case "init":
            if (message.workspaceRoot) {
              document.getElementById("workspaceRoot").textContent =
                "Workspace: " + message.workspaceRoot;
            }
            return;
        }
      });

      const appendLog = (value) => {
        logEl.textContent += value;
        logEl.scrollTop = logEl.scrollHeight;
      };

      showSection(commandSelect.value);
    </script>
  </body>
</html>`;
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
