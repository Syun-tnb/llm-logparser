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
        )
    );
}

export function deactivate() {}
