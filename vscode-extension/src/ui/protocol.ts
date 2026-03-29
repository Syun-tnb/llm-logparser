import type {
  CliExecutionErrorType,
  CliRunPayload,
  CliUiError,
} from "../backend/python";

export type PickKind = "file" | "folder";
export type ViewMode = "parse" | "view";
export type RunPresetValue = string | boolean;

export type ViewerErrorCode =
  | "workspaceRequired"
  | "noFile"
  | "outsideWorkspace"
  | "listFailed"
  | "readFailed"
  | "rootInvalid";

export interface ViewerListEntry {
  path: string;
  name: string;
  display: string;
}

export interface ViewerMessage {
  role: string;
  ts?: number;
  text: string;
  model?: string;
}

export interface ViewerFileData {
  path: string;
  display?: string;
  meta?: {
    provider_id?: string;
    conversation_id?: string;
    message_count?: number;
  };
  messages: ViewerMessage[];
}

export interface ViewerState {
  root?: string;
  files: ViewerListEntry[];
  selectedPath?: string;
  file?: ViewerFileData;
  error?: {
    code: ViewerErrorCode;
    detail?: string;
  };
}

export interface ViewerConfig {
  language: "en" | "ja";
  timezone: "local" | "utc";
  timestampFormat: "relative" | "absolute";
  wrap: boolean;
  showSystem: boolean;
  showToolCalls: boolean;
  compactMode: boolean;
  codeTheme: "auto" | "light" | "dark";
  maxMessagesPerThread: number;
  search: {
    caseSensitive: boolean;
    useRegex: boolean;
  };
}

export interface SalvageItem {
  id: string;
  label: string;
  detail?: string;
  timestamp?: number;
}

export interface SalvageState {
  recentTopics: SalvageItem[];
  resumeCandidates: SalvageItem[];
}

export interface RunState {
  busy: boolean;
  lastExitCode?: number;
  lastError?: CliUiError;
}

export interface PickMessage {
  type: "pick";
  payload: {
    targetId: string;
    kind: PickKind;
  };
}

export interface RunMessage {
  type: "run";
  payload: CliRunPayload;
}

export interface ValidationState {
  command: CliRunPayload["command"];
  fields: string[];
}

export interface RefreshFilesMessage {
  type: "refresh-files";
  payload?: {
    root?: string;
  };
}

export interface OpenViewerFileMessage {
  type: "open-viewer-file";
  payload: {
    path: string;
  };
}

export interface ClearLogRequestMessage {
  type: "clear-log";
}

export interface ResumeRunMessage {
  type: "resume-run";
  payload: {
    id: string;
  };
}

export type WebviewToExtensionMessage =
  | PickMessage
  | RunMessage
  | RefreshFilesMessage
  | OpenViewerFileMessage
  | ClearLogRequestMessage
  | ResumeRunMessage;

export interface InitMessage {
  type: "init";
  workspaceRoot?: string;
  runState: RunState;
  viewerState: ViewerState;
  salvageState: SalvageState;
}

export interface ConfigMessage {
  type: "config" | "config-changed";
  config: ViewerConfig;
  i18n: Record<string, string>;
}

export interface PickResultMessage {
  type: "pick-result";
  targetId: string;
  value: string;
}

export interface ApplyRunPresetMessage {
  type: "apply-run-preset";
  preset: {
    command: CliRunPayload["command"];
    values: Partial<Record<string, RunPresetValue>>;
  };
}

export interface BusyMessage {
  type: "busy";
  value: boolean;
}

export interface LogMessage {
  type: "log";
  value: string;
}

export interface RunFinishedMessage {
  type: "run-finished";
  exitCode: number;
}

export interface RunFailedMessage {
  type: "run-failed";
  errorType: CliExecutionErrorType;
  what: string;
  why: string;
  nextStep: string;
}

export interface ViewerStateMessage {
  type: "viewer-state";
  state: ViewerState;
}

export interface SetModeMessage {
  type: "set-mode";
  mode: ViewMode;
}

export interface ValidationStateMessage {
  type: "validation-state";
  state: ValidationState;
}

export interface SalvageStateMessage {
  type: "salvage-state";
  state: SalvageState;
}

export interface ClearLogMessage {
  type: "clear-log";
}

export type ExtensionToWebviewMessage =
  | InitMessage
  | ConfigMessage
  | ApplyRunPresetMessage
  | PickResultMessage
  | BusyMessage
  | LogMessage
  | RunFinishedMessage
  | RunFailedMessage
  | ViewerStateMessage
  | SetModeMessage
  | ValidationStateMessage
  | SalvageStateMessage
  | ClearLogMessage;
