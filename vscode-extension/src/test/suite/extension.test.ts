import * as assert from "assert";
import * as path from "path";
import * as vscode from "vscode";
import { suite, setup, teardown, test } from "mocha";
import { __panelTestApi, LogParserPanel } from "../../ui/panel";

const EXTENSION_ID = "llm-logparser.llm-logparser-analyzer";

suite("LLM Log Parser extension", () => {
  setup(async () => {
    const extension = vscode.extensions.getExtension(EXTENSION_ID);
    assert.ok(extension, `Extension ${EXTENSION_ID} should be available.`);
    await extension?.activate();
  });

  teardown(() => {
    __panelTestApi.resetRuntime();
    LogParserPanel.currentPanel?.dispose();
  });

  test("registers the main user-facing commands", async () => {
    const commands = await vscode.commands.getCommands(true);

    assert.ok(commands.includes("llmLogparser.openDashboard"));
    assert.ok(commands.includes("llmLogparser.openFromExplorer"));
  });

  test("opens the panel from the main and explorer entry points", async () => {
    await vscode.commands.executeCommand("llmLogparser.openDashboard");
    assert.ok(LogParserPanel.currentPanel);

    LogParserPanel.currentPanel?.dispose();

    const explorerTarget = vscode.Uri.file(
      path.join(
        vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "",
        "sample.jsonl"
      )
    );

    await vscode.commands.executeCommand(
      "llmLogparser.openFromExplorer",
      explorerTarget
    );

    assert.ok(LogParserPanel.currentPanel);
  });

  test("routes parse runs through the mocked CLI boundary", async () => {
    let formattedRequest:
      | { command: string; args: string[] }
      | undefined;
    let executedRequest:
      | { command: string; args: string[] }
      | undefined;
    let executedOptions:
      | { cwd: string; pythonPath: string; cliCommand?: string }
      | undefined;

    __panelTestApi.setRuntime({
      formatCliCommandLine: async (request) => {
        formattedRequest = {
          command: request.command,
          args: [...request.args],
        };
        return "mock-parse-command";
      },
      runCli: async (request, options) => {
        executedRequest = {
          command: request.command,
          args: [...request.args],
        };
        executedOptions = {
          cwd: options.cwd,
          pythonPath: options.pythonPath,
          cliCommand: options.cliCommand,
        };
        return 0;
      },
    });

    await vscode.commands.executeCommand("llmLogparser.openDashboard");
    const panel = LogParserPanel.currentPanel;
    assert.ok(panel);

    await __panelTestApi.dispatchMessage(panel!, {
      type: "run",
      payload: {
        command: "parse",
        options: {
          provider: "openai",
          input: "sample.jsonl",
          outdir: "artifacts",
        },
      },
    });

    assert.deepStrictEqual(formattedRequest, {
      command: "parse",
      args: ["--provider", "openai", "--input", "sample.jsonl", "--outdir", "artifacts"],
    });
    assert.deepStrictEqual(executedRequest, formattedRequest);
    assert.ok(executedOptions);
    assert.strictEqual(
      executedOptions?.cwd,
      vscode.workspace.workspaceFolders?.[0]?.uri.fsPath
    );
  });

  test("builds analyze stats requests through the same mocked flow", async () => {
    let executedRequest:
      | { command: string; args: string[] }
      | undefined;

    __panelTestApi.setRuntime({
      formatCliCommandLine: async (request) =>
        ["mock-analyze", request.command, ...request.args].join(" "),
      runCli: async (request) => {
        executedRequest = {
          command: request.command,
          args: [...request.args],
        };
        return 0;
      },
    });

    await vscode.commands.executeCommand("llmLogparser.openDashboard");
    const panel = LogParserPanel.currentPanel;
    assert.ok(panel);

    await __panelTestApi.dispatchMessage(panel!, {
      type: "run",
      payload: {
        command: "analyze",
        options: {
          analyzeCommand: "stats",
          input: "artifacts/output/openai",
          json: true,
          perThread: true,
          top: "5",
          sort: "messages",
          includeRoleBreakdown: true,
        },
      },
    });

    assert.deepStrictEqual(executedRequest, {
      command: "analyze",
      args: [
        "stats",
        "--input",
        "artifacts/output/openai",
        "--json",
        "--per-thread",
        "--top",
        "5",
        "--sort",
        "messages",
        "--include-role-breakdown",
      ],
    });
  });
});
