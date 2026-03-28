# LLM LogParser Analysis Instrument

This is the VS Code Extension wrapper for the Python `llm-logparser` CLI. 

It provides an immersive "Liquid" aesthetic instrument directly within the editor to run parses, browse artifacts, and inspect heuristic metrics inside exported JSONL strings without breaking your development flow.

## Local Development (Testing on Mac M4)

1. Open this extension folder in VS Code:
   ```bash
   code /Users/tanabeshunji/Documents/llm-logparser/vscode-extension
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Press `F5` to open a new VS Code Extension Development Host window.
4. From the Command Palette (`Cmd+Shift+P`), run **"Open LogParser Instrument"** to launch the Main Stage Webview Panel.

*Alternatively, click the new LogParser icon in the VS Code Activity bar to view the Sidebar Webview.*
