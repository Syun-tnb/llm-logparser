import * as vscode from 'vscode';
import * as path from 'path';

export class DashboardPanel {
    public static currentPanel: DashboardPanel | undefined;
    private readonly _panel: vscode.WebviewPanel;
    private _disposables: vscode.Disposable[] = [];

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
        this._panel = panel;
        this._panel.onDidDispose(() => this.dispose(), null, this._disposables);
        this._panel.webview.html = this._getHtmlForWebview();
        
        // Setup Workspace Watcher
        this._setupFileWatcher();
    }

    public static createOrShow(extensionUri: vscode.Uri) {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;

        if (DashboardPanel.currentPanel) {
            DashboardPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            'llmLogparserDashboard',
            'Log Analysis Instrument',
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [extensionUri]
            }
        );

        DashboardPanel.currentPanel = new DashboardPanel(panel, extensionUri);
    }

    private _setupFileWatcher() {
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders) return;
        
        // Listen for changes to jsonl files assuming mock location
        const pattern = new vscode.RelativePattern(workspaceFolders[0], '**/*.jsonl');
        const watcher = vscode.workspace.createFileSystemWatcher(pattern);
        
        watcher.onDidChange(uri => {
            this._panel.webview.postMessage({ command: 'refresh', file: uri.fsPath });
        });
        
        this._disposables.push(watcher);
    }

    public dispose() {
        DashboardPanel.currentPanel = undefined;
        this._panel.dispose();
        while (this._disposables.length) {
            const x = this._disposables.pop();
            if (x) x.dispose();
        }
    }

    private _getHtmlForWebview() {
        // Here we embed the full main-stage HTML directly as requested,
        // Using body.vscode-light to map the Theme Toggle automatically natively to VS Code themes!
        return `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
    :root {
      --vscode-font-family: var(--vscode-editor-font-family, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto);
      --vscode-mono-font: var(--vscode-editor-font-family, "JetBrains Mono", "Fira Code", monospace);
      
      --liquid-radius-sm: 4px; --liquid-radius-md: 8px; --liquid-radius-lg: 12px;
      --glass-blur: blur(20px); --center-blur: blur(40px);
      
      /* Base Defaults mapped to deeply dark */
      --bg-app: #0f111a;
      --bg-panel: rgba(20, 20, 25, 0.6);
      --bg-surface: rgba(255, 255, 255, 0.03);
      --bg-terminal: #050505;
      --border-color: rgba(255, 255, 255, 0.08);
      --text-main: #e0e0e0;
      --text-muted: rgba(255, 255, 255, 0.5);
      --text-header: var(--vscode-foreground, #fff);
      
      --color-primary: #0a84ff;
      --color-critical: #ff453a; --color-warning: #ffd60a; --color-success: #32d74b; --color-terminal: #a6e22e;
      --badge-bg-crit: rgba(255,69,58,0.15); --badge-bg-warn: rgba(255,214,10,0.15);
      
      --shadow-panel: 0 10px 40px rgba(0,0,0,0.4);
    }

    /* Map Light Theme to native VS Code injected class! */
    body.vscode-light {
      --bg-app: #e0e0e2;               
      --bg-panel: rgba(245, 245, 247, 0.85); 
      --bg-surface: #ffffff;           
      --bg-terminal: #050505;          
      --border-color: rgba(0, 0, 0, 0.12);
      
      --text-main: #121212;            
      --text-muted: #5e5e5e;           
      --text-header: #000;
      
      --color-primary: #005bb5;
      
      --color-critical: #9e1c23;       
      --color-warning: #b35900;        
      --color-success: #1b6329;        
      --color-terminal: #a6e22e;       
      
      --badge-bg-crit: rgba(158, 28, 35, 0.1);
      --badge-bg-warn: rgba(179, 89, 0, 0.1);
      
      --shadow-panel: 0 4px 16px rgba(0,0,0,0.06);
    }

    body {
        background: var(--bg-app);
        color: var(--text-main);
        font-family: var(--vscode-font-family);
        padding: 0; margin: 0;
        height: 100vh; display: flex; flex-direction: column; overflow: hidden;
        transition: all 0.6s cubic-bezier(0.16, 1, 0.3, 1);
    }
    
    * { box-sizing: border-box; }

    .main-stage { flex: 1; display: flex; flex-direction: column; padding: 24px; }
    
    .tabs-header { display: flex; gap: 4px; margin-bottom: 12px; }
    .tab {
      padding: 8px 16px; border-radius: var(--liquid-radius-sm) var(--liquid-radius-sm) 0 0;
      background: var(--bg-surface); border: 1px solid var(--border-color); border-bottom: none;
      font-family: var(--vscode-mono-font); font-size: 0.75rem; cursor: pointer; color: var(--text-muted);
    }
    .tab.active { background: var(--bg-panel); color: var(--text-main); border-top: 2px solid var(--color-primary); font-weight: 600;}

    .view-container {
      flex: 1; display: flex; background: var(--bg-panel);
      backdrop-filter: var(--center-blur); -webkit-backdrop-filter: var(--center-blur);
      border: 1px solid var(--border-color); border-radius: 0 var(--liquid-radius-lg) var(--liquid-radius-lg) var(--liquid-radius-lg);
      box-shadow: var(--shadow-panel); overflow: hidden;
    }

    .split-view { display: flex; width: 100%; height: 100%; }
    .gfm-viewer { flex: 2; overflow-y: auto; padding: 24px; border-right: 1px solid var(--border-color); }
    .artifacts-sidecar { width: 300px; background: var(--bg-surface); overflow-y: auto; padding: 16px; border-left: 1px solid var(--border-color);}

    .msg-panel { margin-bottom: 16px; border-left: 3px solid transparent; padding-left: 12px; }
    .msg-user { border-left-color: var(--text-muted); }
    .msg-assistant { border-left-color: var(--color-primary); }

    .msg-meta { font-family: var(--vscode-mono-font); font-size: 0.7rem; color: var(--text-muted); margin-bottom: 6px; display: flex; justify-content: space-between;}
    
    .msg-content {
      background: var(--bg-surface); border: 1px solid var(--border-color);
      border-radius: var(--liquid-radius-md); padding: 16px; font-size: 0.85rem; line-height: 1.5;
    }

    .msg-content pre {
      background: var(--bg-terminal); padding: 10px; border-radius: 4px; font-family: var(--vscode-mono-font);
      font-size: 0.75rem; overflow-x: auto; border: 1px solid rgba(0,0,0,0.2); margin: 8px 0;
      color: rgba(255,255,255,0.9);
    }
    .msg-content code { color: var(--color-terminal); }

    .heuristic {
      display: inline-block; padding: 2px 6px; border-radius: 4px; font-family: var(--vscode-mono-font);
      font-size: 0.65rem; font-weight: 700; margin-right: 6px; cursor: help; vertical-align: middle; border: 1px solid transparent;
    }
    .heur-refusal { background: var(--badge-bg-crit); color: var(--color-critical); border-color: var(--color-critical); }

    .l1-card {
      background: var(--bg-surface); border: 1px solid var(--border-color);
      border-radius: var(--liquid-radius-sm); padding: 12px; margin-bottom: 12px;
    }
    .l1-title { font-family: var(--vscode-mono-font); font-size: 0.65rem; color: var(--text-header); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; font-weight:600;}
    
    .metric-row { display: flex; justify-content: space-between; font-family: var(--vscode-mono-font); font-size: 0.75rem; margin-bottom: 4px; }
    .metric-key { color: var(--text-muted); }
    .metric-val { color: var(--text-main); font-weight: 600;}
    </style>
</head>
<body>
    <div class="main-stage">
      <div class="tabs-header">
        <div class="tab active">Muted Instrument Stage (GFM)</div>
        <div class="tab">Raw Artifacts Peek</div>
      </div>

      <div class="view-container">
        <div class="split-view">
          <div class="gfm-viewer">
            
            <div class="msg-panel msg-user">
              <div class="msg-meta"><span>USER</span><span>ID: live_thread_1</span></div>
              <div class="msg-content">Review this injected code for parsing weaknesses.</div>
            </div>

            <div class="msg-panel msg-assistant">
              <div class="msg-meta"><span>ASSISTANT</span></div>
              <div class="msg-content">
                <span class="heuristic heur-refusal">safety.refusal</span>
                I cannot fulfill that query due to restricted policy bounds.
              </div>
            </div>

          </div>

          <div class="artifacts-sidecar">
            <div class="section-title" style="font-size:0.7rem; color:var(--text-muted); font-family:monospace; margin-bottom:12px;">Thread Artifacts (L1)</div>

            <div class="l1-card">
              <div class="l1-title">📄 token_stats.json</div>
              <div class="metric-row"><span class="metric-key">total_tokens:</span><span class="metric-val">1204</span></div>
              <div class="metric-row"><span class="metric-key">prompt_tokens:</span><span class="metric-val">602</span></div>
            </div>

            <div class="l1-card" style="border-left: 2px solid var(--color-critical);">
              <div class="l1-title" style="color:var(--color-critical);">🚨 metrics.json</div>
              <div class="metric-row"><span class="metric-key">safety_flagged:</span><span class="metric-val" style="color:var(--color-critical)">true</span></div>
              <div class="metric-row"><span class="metric-key">heuristic_refusals:</span><span class="metric-val">1</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <script>
      const vscode = acquireVsCodeApi();
      window.addEventListener('message', event => {
          const message = event.data;
          // React to file watcher refreshes here!
          if(message.command === 'refresh') {
             console.log("Workspace artifact changed, reloading log states.");
             // Document reload logic
          }
      });
    </script>
</body>
</html>`;
    }
}
