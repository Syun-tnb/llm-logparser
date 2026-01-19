import * as vscode from "vscode";
import { LogParserPanel } from "./ui/panel";

export function activate(context: vscode.ExtensionContext): void {
  const command = vscode.commands.registerCommand("llmLogparser.openPanel", () => {
    LogParserPanel.createOrShow(context.extensionUri);
  });

  context.subscriptions.push(command);
}

export function deactivate(): void {}
