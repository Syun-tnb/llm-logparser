(() => {
  const vscode = acquireVsCodeApi();

  const logEl = document.getElementById("log");
  const commandSelect = document.getElementById("command");
  const runButton = document.getElementById("run");
  const clearButton = document.getElementById("clear");
  const workspaceRootEl = document.getElementById("workspaceRoot");

  const sections = {
    parse: document.getElementById("section-parse"),
    export: document.getElementById("section-export"),
    chain: document.getElementById("section-chain"),
  };

  const showSection = (command) => {
    Object.entries(sections).forEach(([key, element]) => {
      if (!element) {
        return;
      }
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

  runButton?.addEventListener("click", () => {
    const payload = collectPayload(commandSelect?.value ?? "parse");
    vscode.postMessage({ type: "run", payload });
  });

  clearButton?.addEventListener("click", () => {
    vscode.postMessage({ type: "clear-log" });
  });

  commandSelect?.addEventListener("change", (event) => {
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

  window.addEventListener("message", (event) => {
    const message = event.data;
    switch (message.type) {
      case "log":
        appendLog(message.value);
        return;
      case "clear-log":
        if (logEl) {
          logEl.textContent = "";
        }
        return;
      case "pick-result":
        if (message.targetId) {
          const target = document.getElementById(message.targetId);
          if (target) {
            target.value = message.value ?? "";
          }
        }
        return;
      case "busy":
        if (runButton) {
          runButton.disabled = Boolean(message.value);
        }
        return;
      case "run-error":
        appendLog(`\n[error] ${message.message}\n`);
        return;
      case "init":
        if (workspaceRootEl && message.workspaceRoot) {
          workspaceRootEl.textContent = `Workspace: ${message.workspaceRoot}`;
        }
        return;
    }
  });

  const appendLog = (value) => {
    if (!logEl) {
      return;
    }
    logEl.textContent += value;
    logEl.scrollTop = logEl.scrollHeight;
  };

  showSection(commandSelect?.value ?? "parse");
})();
