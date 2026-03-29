import * as vscode from 'vscode';
import { LogParserPanel } from './ui/panel';

export function activate(context: vscode.ExtensionContext) {
    console.log('llm-logparser-analyzer is active.');
    const outputChannel = vscode.window.createOutputChannel('LLM LogParser');

    context.subscriptions.push(
        outputChannel,
        vscode.commands.registerCommand(
            'llmLogparser.openDashboard',
            () => {
                LogParserPanel.createOrShow(context.extensionUri, outputChannel);
            }
        ),
        vscode.commands.registerCommand(
            'llmLogparser.openFromExplorer',
            (resource?: vscode.Uri) => {
                if (!resource || resource.scheme !== 'file') {
                    LogParserPanel.createOrShow(context.extensionUri, outputChannel);
                    return;
                }

                const panel = LogParserPanel.createOrShow(context.extensionUri, outputChannel);
                panel.showWithInput(resource.fsPath);
            }
        )
    );
}

export function deactivate() {}
