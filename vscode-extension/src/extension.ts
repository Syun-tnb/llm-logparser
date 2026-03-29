import * as vscode from 'vscode';
import { LogParserPanel } from './ui/panel';

export function activate(context: vscode.ExtensionContext) {
    console.log('llm-logparser-analyzer is active.');

    context.subscriptions.push(
        vscode.commands.registerCommand(
            'llmLogparser.openDashboard',
            () => {
                LogParserPanel.createOrShow(context.extensionUri);
            }
        ),
        vscode.commands.registerCommand(
            'llmLogparser.openFromExplorer',
            (resource?: vscode.Uri) => {
                if (!resource || resource.scheme !== 'file') {
                    LogParserPanel.createOrShow(context.extensionUri);
                    return;
                }

                const panel = LogParserPanel.createOrShow(context.extensionUri);
                panel.showWithInput(resource.fsPath);
            }
        )
    );
}

export function deactivate() {}
