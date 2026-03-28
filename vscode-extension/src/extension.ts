import * as vscode from 'vscode';
import { SidebarProvider } from './SidebarProvider';
import { DashboardPanel } from './DashboardPanel';

export function activate(context: vscode.ExtensionContext) {
    console.log('llm-logparser-analyzer is active.');

    // Register Sidebar
    const sidebarProvider = new SidebarProvider(context.extensionUri);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            "llmLogparser.sidebarView",
            sidebarProvider
        )
    );

    // Register Command to Open Main Dashboard
    const openDashboardCommand = vscode.commands.registerCommand(
        'llmLogparser.openDashboard', 
        () => {
            DashboardPanel.createOrShow(context.extensionUri);
        }
    );
    context.subscriptions.push(openDashboardCommand);
    
    // Command to parse L1 via sidebar logic
    context.subscriptions.push(
        vscode.commands.registerCommand('llmLogparser.runParse', () => {
            sidebarProvider.executeCliCommand('parse');
        })
    );
}

export function deactivate() {}
