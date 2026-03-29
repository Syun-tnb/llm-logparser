## Overview

**LLM Log Parser** is a VS Code extension for browsing and analyzing LLM interaction logs stored in JSONL files.

It solves a practical workflow problem: raw log files are hard to inspect in a text editor once they become large, multi-threaded, or Markdown-heavy. This extension provides a packaged webview UI inside VS Code for reading parsed conversations, filtering large threads, running lightweight analysis, and resuming recent work without switching to the CLI.

## Features

- Browse parsed conversation logs in a dedicated Viewer mode
- Render message content as readable GitHub-Flavored Markdown (GFM)
- Filter threads by text and role
- Incrementally load large threads instead of rendering everything at once
- Run a focused set of analysis actions from the UI
- Surface recent topics and resume candidates to help continue unfinished work
- Preserve in-progress UI state across panel hide/show
- Keep heavy file loading and execution logic on the extension side
- Package all webview runtime assets into `dist` for reliable VSIX behavior

## Setup

1. Install the extension in VS Code.
2. Open the workspace that contains your parsed LLM log files.
3. Open the command palette.
4. Run `LLM LogParser: Open LogParser`.
5. In the panel:
   - use **Viewer** mode to browse parsed JSONL threads
   - use **Analyze** mode to run supported inspection commands
6. If needed, set the root directory that contains your parsed output before browsing files.

## Configuration

The extension is designed to work with lightweight, workspace-oriented settings.

| Setting | Purpose | Typical Use |
| --- | --- | --- |
| `llmLogparser.pythonPath` | Python executable used when running CLI-backed actions | Set this if your workspace uses a specific Python interpreter |
| `llmLogparser.cliCommand` | Explicit command override for launching the CLI | Use this when you want to force a custom launcher such as a managed environment command |
| `llmLogparser.viewer.language` | UI language for the panel (`auto`, `en`, `ja`) | Leave as `auto` to follow VS Code, or force English/Japanese |
| Viewer root | Default directory used to discover parsed files | Point this at the folder containing `parsed.jsonl` files |
| Max visible messages | Limits how many messages are shown initially in large threads | Helps keep large conversations responsive |
| Auto refresh | Refreshes file lists when entering View mode or after output-producing actions | Useful when parse/analyze results are generated inside the workspace |

## Usage

### Viewer Mode

Use **Viewer** mode to inspect parsed conversation threads.

Typical flow:

1. Open the panel.
2. Switch to **View**.
3. Set the root directory if needed.
4. Select a `parsed.jsonl` file from the file list.
5. Read the conversation chronologically:
   - each message shows role, timestamp, and content
   - message content is rendered as Markdown
6. Use the built-in controls to:
   - search by text
   - filter by role
   - load older messages incrementally

The viewer is optimized for real logs, not just small demos. Large threads are trimmed initially and expanded on demand.

### Analyze Mode

Use **Analyze** mode when you want structured inspection rather than thread reading.

Typical flow:

1. Open the panel.
2. Choose **Analyze** from the command selector.
3. Select the analysis subcommand you want to run.
4. Provide the required input path.
5. Run the command from the panel.

Analyze mode is intended to expose a useful subset of inspection features without requiring direct CLI usage.

## State Persistence

The panel preserves lightweight UI state across hide/show using VS Code webview state persistence (`getState()` / `setState()`).

This includes:

- current mode
- selected command
- form field values
- analyze subcommand selection
- viewer root and filters
- selected viewer file when it can be restored

The extension does **not** store large thread payloads or full log output in webview state. Heavy data remains owned by the extension backend and is reloaded when needed.

## Fallback Behavior

If the current session cannot be restored exactly, the extension falls back to safe, predictable behavior.

- **No root directory is set**
  - The viewer shows no files until a valid root is chosen.
  - The panel remains usable, but file browsing is limited.

- **A file is missing**
  - The viewer does not silently fail.
  - File loading falls back to the available file list and shows the current viewer state without restoring missing content.

- **Stored state is invalid**
  - Invalid or outdated webview state is ignored.
  - The panel restores what it can and falls back to default UI state for the rest.

## Notes / Design Philosophy

- **Lightweight runtime**
  - The webview keeps only compact UI state.
  - Large file contents are not cached in webview persistence.

- **Packaged for VSIX stability**
  - Runtime HTML, CSS, JavaScript, i18n files, and browser-side vendor assets are copied into `dist`.
  - The packaged extension does not depend on `src/` or `node_modules/` at runtime.

- **Clear separation of responsibilities**
  - The webview owns presentation and lightweight session state.
  - The extension backend owns file loading, execution, and authoritative data flow.

## Screenshots

_Screenshots coming soon._

- Viewer mode
- Analyze mode
- Resume candidates
- Filtered thread view