import * as vscode from 'vscode';
import { spawn } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

export class SidebarProvider implements vscode.WebviewViewProvider {
    _view?: vscode.WebviewView;

    constructor(private readonly _extensionUri: vscode.Uri) {}

    public resolveWebviewView(webviewView: vscode.WebviewView) {
        this._view = webviewView;
        webviewView.webview.options = { 
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };

        webviewView.webview.html = this._getHtmlForWebview();

        webviewView.webview.onDidReceiveMessage((message) => {
            switch (message.command) {
                case 'execute':
                    this.executeCliCommand(message.type);
                    break;
                case 'openDashboard':
                    vscode.commands.executeCommand('llmLogparser.openDashboard');
                    break;
            }
        });
    }

    public async executeCliCommand(type: string) {
        if (!this._view) { return; }
        
        let cwd: string;
        if (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders.length > 0) {
            cwd = vscode.workspace.workspaceFolders[0].uri.fsPath;
        } else {
            this.streamLog("No active workspace. Prompting...", "info");
            const folderUri = await vscode.window.showOpenDialog({
                canSelectFolders: true,
                canSelectFiles: false,
                canSelectMany: false,
                openLabel: 'Select Workspace to Parse'
            });
            if (!folderUri || folderUri.length === 0) {
                this.streamLog("Execution cancelled. No folder selected.", "error");
                return;
            }
            cwd = folderUri[0].fsPath;
        }
        
        const hasVenv = fs.existsSync(path.join(cwd, '.venv'));
        const hasPyproject = fs.existsSync(path.join(cwd, 'pyproject.toml'));

        if (!hasVenv && !hasPyproject) {
            this.streamLog('Error: Neither .venv nor pyproject.toml found in workspace root. "uv" requires a project environment.', 'error');
            return;
        }

        let commandStr = `uv run llp ${type}`;
        if(type === 'chain') commandStr = 'uv run llp parse && uv run llp export';

        this.streamLog(`[SYSTEM] Starting execution in ${cwd}...`, 'info');
        this.streamLog(`> ${commandStr}`, 'info');
        
        const { exec } = require('child_process');
        exec('uv --version', { cwd }, (err: any) => {
            if (err) {
                this.streamLog('Error: "uv" executable not found in PATH or environment.', 'error');
                return;
            }

            // Execute actual command through uv
            const child = spawn(commandStr, { cwd, shell: true });

            child.stdout.on('data', (data) => {
                this.streamLog(data.toString().trim(), 'info');
            });

            child.stderr.on('data', (data) => {
                this.streamLog(data.toString().trim(), 'error');
            });

            child.on('close', (code) => {
                this.streamLog(`Exited with code ${code}`, code === 0 ? 'success' : 'error');
                if(code === 0) {
                  vscode.window.showInformationMessage("LogParser execution finished.");
                }
            });
        });
    }

    private streamLog(msg: string, type: string) {
        this._view?.webview.postMessage({
            command: 'log',
            message: msg,
            type: type
        });
    }

    private _getHtmlForWebview() {
        // Incorporating the Sidebar portion of the unified index.html
        return `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root {
            /* Syncing to VS Code global variables */
            --bg-sidebar: var(--vscode-sideBar-background);
            --bg-surfac: rgba(255, 255, 255, 0.05);
            --border-color: var(--vscode-sideBarSectionHeader-border);
            --text-main: var(--vscode-foreground);
            --text-muted: var(--vscode-descriptionForeground);
            --color-primary: var(--vscode-button-background);
            --color-terminal: var(--vscode-terminal-ansiGreen);
            
            --liquid-radius-sm: 6px;
        }

        body.vscode-light {
            --bg-surface: rgba(0, 0, 0, 0.03);
            --color-terminal: var(--vscode-terminal-ansiGreen);
        }

        body {
            font-family: var(--vscode-font-family);
            color: var(--text-main);
            padding: 0; margin: 0;
            display: flex; flex-direction: column; height: 100vh;
        }

        .section { padding: 12px; border-bottom: 1px solid var(--border-color); }
        .title { font-size: 0.7rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px; font-family: monospace;}
        
        .tree-item {
            font-size: 0.8rem; padding: 4px; cursor: pointer; display:flex; align-items:center; gap:6px;
            color: var(--text-muted); border-radius: var(--liquid-radius-sm);
        }
        .tree-item:hover { background: var(--bg-surface); color: var(--text-main); }
        .tree-item.active { background: rgba(0, 122, 255, 0.15); color: #0a84ff; }
        
        .btn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 8px;}
        .btn {
            background: var(--bg-surface); border: 1px solid var(--border-color); color: var(--text-main);
            padding: 8px; border-radius: var(--liquid-radius-sm); cursor: pointer; text-align: center;
            transition: all 0.2s; font-size: 0.75rem;
        }
        .btn:hover { background: var(--color-primary); color: white; border-color: transparent;}
        .btn-chain { grid-column: 1 / -1; font-weight: bold; background: linear-gradient(135deg, rgba(0, 122, 255, 0.2), rgba(94, 92, 230, 0.2)); }
        
        .stream { flex: 1; background: var(--vscode-terminal-background); padding: 10px; overflow-y: auto; font-family: monospace; font-size: 0.7rem; }
        .log-entry { margin-bottom: 4px; color: var(--text-muted); }
        .log-entry.info { color: var(--text-main); }
        .log-entry.error { color: var(--vscode-terminal-ansiRed); }
        .log-entry.success { color: var(--color-terminal); }

        .btn-dash { width: 100%; padding:10px; margin-top:10px; background: var(--color-primary); color: white; border:none; border-radius: 4px; cursor:pointer;}
    </style>
</head>
<body>
    <div class="section">
        <div class="title">Workspace Exports</div>
        <div class="tree-item active">📝 claude_research.jsonl</div>
        <div class="tree-item">📝 gpt4_base.jsonl</div>
        <button class="btn-dash" onclick="openDashboard()">Open Full Dashboard</button>
    </div>

    <div class="section" style="border-bottom: none;">
        <div class="title">Task Triggers</div>
        <div class="btn-grid">
            <button class="btn" onclick="execute('parse')">Parse L1</button>
            <button class="btn" onclick="execute('export')">Export GFM</button>
            <button class="btn btn-chain" onclick="execute('chain')">Chain Execution</button>
        </div>
    </div>

    <div class="stream" id="log-view">
        <div class="log-entry">> llm-logparser VS Code Ext loaded.</div>
    </div>

    <script>
        const vscode = acquireVsCodeApi();
        const term = document.getElementById('log-view');

        function execute(type) {
            vscode.postMessage({ command: 'execute', type });
        }
        function openDashboard() {
            vscode.postMessage({ command: 'openDashboard' });
        }

        window.addEventListener('message', event => {
            const message = event.data;
            if (message.command === 'log') {
                term.insertAdjacentHTML('beforeend', \`<div class="log-entry \${message.type}">\${message.message}</div>\`);
                term.scrollTop = term.scrollHeight;
            }
        });
    </script>
</body>
</html>`;
    }
}
