import * as fs from "fs";
import * as path from "path";
import { spawn } from "child_process";

export type AnalyzeSubcommand = "stats" | "timeline" | "tokens" | "metrics";
export type CliCommand = "parse" | "export" | "chain" | "analyze";
export type CliOptionValue = string | boolean | undefined;
export type CliOptions = Record<string, CliOptionValue>;
export type CliExecutionErrorType =
  | "MissingWorkspaceError"
  | "BinaryNotFoundError"
  | "PermissionDeniedError"
  | "InvalidInputError"
  | "UnknownExecutionError";

export interface CliRunPayload {
  command: CliCommand;
  options: CliOptions;
}

export interface RunCliRequest {
  command: CliCommand;
  args: string[];
}

export interface RunCliOptions {
  cwd: string;
  pythonPath: string;
  cliCommand?: string;
  env?: NodeJS.ProcessEnv;
  onStdout?: (chunk: string) => void;
  onStderr?: (chunk: string) => void;
}

export interface CliUiError {
  type: CliExecutionErrorType;
  what: string;
  why: string;
  nextStep: string;
}

type CliLaunchStrategy = "cliCommand" | "uv" | "pythonModule";
type ErrorPhase = "preflight" | "runtime";
type CommandProbeResult = "ok" | "missing" | "permissionDenied";

interface ResolvedCliInvocation {
  command: string;
  args: string[];
  env: NodeJS.ProcessEnv;
  strategy: CliLaunchStrategy;
}

class CliExecutionError extends Error {
  public readonly type: CliExecutionErrorType;
  public readonly phase: ErrorPhase;
  public readonly what: string;
  public readonly why: string;
  public readonly nextStep: string;

  constructor(
    type: CliExecutionErrorType,
    phase: ErrorPhase,
    what: string,
    why: string,
    nextStep: string
  ) {
    super(`${what} ${why} ${nextStep}`);
    this.name = type;
    this.type = type;
    this.phase = phase;
    this.what = what;
    this.why = why;
    this.nextStep = nextStep;
  }
}

export class MissingWorkspaceError extends CliExecutionError {
  constructor(phase: ErrorPhase, why?: string, nextStep?: string) {
    super(
      "MissingWorkspaceError",
      phase,
      "No workspace folder is available.",
      why ?? "The LogParser CLI runs against files in the current workspace checkout.",
      nextStep ?? "Open the repository folder in VS Code and run the command again."
    );
  }
}

export class BinaryNotFoundError extends CliExecutionError {
  constructor(phase: ErrorPhase, what: string, why: string, nextStep: string) {
    super("BinaryNotFoundError", phase, what, why, nextStep);
  }
}

export class PermissionDeniedError extends CliExecutionError {
  constructor(phase: ErrorPhase, what: string, why: string, nextStep: string) {
    super("PermissionDeniedError", phase, what, why, nextStep);
  }
}

export class InvalidInputError extends CliExecutionError {
  constructor(phase: ErrorPhase, what: string, why: string, nextStep: string) {
    super("InvalidInputError", phase, what, why, nextStep);
  }
}

export class UnknownExecutionError extends CliExecutionError {
  constructor(phase: ErrorPhase, what: string, why: string, nextStep: string) {
    super("UnknownExecutionError", phase, what, why, nextStep);
  }
}

const PATH_SEPARATOR = process.platform === "win32" ? ";" : ":";
const commandAvailabilityCache = new Map<string, Promise<CommandProbeResult>>();

const appendPath = (existing: string | undefined, nextPath: string): string => {
  if (!existing) {
    return nextPath;
  }
  const parts = existing.split(PATH_SEPARATOR);
  if (parts.includes(nextPath)) {
    return existing;
  }
  return `${nextPath}${PATH_SEPARATOR}${existing}`;
};

const resolveWorkspaceRoot = (cwd: string): string => {
  const trimmed = cwd.trim();
  if (!trimmed) {
    throw new MissingWorkspaceError("preflight");
  }
  return path.resolve(trimmed);
};

const ensureWorkspaceRoot = async (cwd: string): Promise<string> => {
  const workspaceRoot = resolveWorkspaceRoot(cwd);

  try {
    const stats = await fs.promises.stat(workspaceRoot);
    if (!stats.isDirectory()) {
      throw new MissingWorkspaceError(
        "preflight",
        "The configured workspace root does not point to a folder on disk.",
        "Open the repository root as a folder in VS Code and run the command again."
      );
    }
    return workspaceRoot;
  } catch (error) {
    if (error instanceof CliExecutionError) {
      throw error;
    }
    throw new MissingWorkspaceError(
      "preflight",
      "The configured workspace root does not exist on disk anymore.",
      "Reopen the repository folder in VS Code and run the command again."
    );
  }
};

const getWorkspaceSrcPath = (workspaceRoot: string): string =>
  path.join(workspaceRoot, "src");

const getPyprojectPath = (workspaceRoot: string): string =>
  path.join(workspaceRoot, "pyproject.toml");

const fileExists = async (target: string): Promise<boolean> => {
  try {
    await fs.promises.access(target, fs.constants.F_OK);
    return true;
  } catch (error) {
    return false;
  }
};

const hasWorkspacePyproject = async (workspaceRoot: string): Promise<boolean> =>
  fileExists(getPyprojectPath(workspaceRoot));

const buildBaseEnv = (
  options: RunCliOptions,
  workspaceRoot: string
): NodeJS.ProcessEnv => {
  const env = { ...process.env, ...options.env };
  env.PYTHONPATH = appendPath(env.PYTHONPATH, getWorkspaceSrcPath(workspaceRoot));
  return env;
};

const valueAsString = (value: unknown): string | undefined => {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
};

const valueAsBoolean = (value: unknown): boolean => Boolean(value);

const buildCliArgs = (payload: CliRunPayload): string[] => {
  const args: string[] = [];
  const opts = payload.options;

  const add = (flag: string, value?: string) => {
    if (value) {
      args.push(flag, value);
    }
  };

  const addFlag = (flag: string, enabled: boolean) => {
    if (enabled) {
      args.push(flag);
    }
  };

  if (payload.command === "parse") {
    add("--provider", valueAsString(opts.provider));
    add("--input", valueAsString(opts.input));
    add("--outdir", valueAsString(opts.outdir));
    addFlag("--dry-run", valueAsBoolean(opts.dryRun));
    addFlag("--fail-fast", valueAsBoolean(opts.failFast));
    addFlag("--validate-schema", valueAsBoolean(opts.validateSchema));
  } else if (payload.command === "export") {
    add("--input", valueAsString(opts.input));
    add("--out", valueAsString(opts.out));
    add("--timezone", valueAsString(opts.timezone));
    add("--formatting", valueAsString(opts.formatting));
    add("--split", valueAsString(opts.split));
    add("--split-soft-overflow", valueAsString(opts.splitSoftOverflow));
    addFlag("--split-hard", valueAsBoolean(opts.splitHard));
    addFlag("--split-preview", valueAsBoolean(opts.splitPreview));
    add("--tiny-tail-threshold", valueAsString(opts.tinyTailThreshold));
  } else if (payload.command === "chain") {
    add("--provider", valueAsString(opts.provider));
    add("--input", valueAsString(opts.input));
    add("--outdir", valueAsString(opts.outdir));
    add("--timezone", valueAsString(opts.timezone));
    add("--formatting", valueAsString(opts.formatting));
    add("--split", valueAsString(opts.split));
    add("--split-soft-overflow", valueAsString(opts.splitSoftOverflow));
    addFlag("--split-hard", valueAsBoolean(opts.splitHard));
    addFlag("--split-preview", valueAsBoolean(opts.splitPreview));
    add("--tiny-tail-threshold", valueAsString(opts.tinyTailThreshold));
    add("--export-outdir", valueAsString(opts.exportOutdir));
    add("--parsed-root", valueAsString(opts.parsedRoot));
    addFlag("--dry-run", valueAsBoolean(opts.dryRun));
    addFlag("--fail-fast", valueAsBoolean(opts.failFast));
    addFlag("--validate-schema", valueAsBoolean(opts.validateSchema));
  } else if (payload.command === "analyze") {
    const analyzeCommand = valueAsString(opts.analyzeCommand);
    if (analyzeCommand) {
      args.push(analyzeCommand);
    }
    add("--input", valueAsString(opts.input));

    if (analyzeCommand === "stats") {
      addFlag("--json", valueAsBoolean(opts.json));
      add("--out", valueAsString(opts.out));
      addFlag("--per-thread", valueAsBoolean(opts.perThread));
      add("--top", valueAsString(opts.top));
      add("--sort", valueAsString(opts.sort));
      addFlag(
        "--include-role-breakdown",
        valueAsBoolean(opts.includeRoleBreakdown)
      );
    } else if (analyzeCommand === "timeline") {
      add("--bucket", valueAsString(opts.bucket));
      addFlag("--json", valueAsBoolean(opts.json));
      add("--out", valueAsString(opts.out));
    } else if (analyzeCommand === "tokens") {
      add("--model", valueAsString(opts.model));
      add("--encoding", valueAsString(opts.encoding));
      addFlag("--skip-existing", valueAsBoolean(opts.skipExisting));
      addFlag("--dry-run", valueAsBoolean(opts.dryRun));
    } else if (analyzeCommand === "metrics") {
      addFlag("--skip-existing", valueAsBoolean(opts.skipExisting));
      addFlag("--dry-run", valueAsBoolean(opts.dryRun));
    }
  }

  return args;
};

const tokenizeCommand = (commandLine: string): string[] => {
  const trimmed = commandLine.trim();
  if (!trimmed) {
    return [];
  }

  const tokens: string[] = [];
  let current = "";
  let quote: "'" | '"' | undefined;
  let escaping = false;

  for (const char of trimmed) {
    if (escaping) {
      current += char;
      escaping = false;
      continue;
    }

    if (quote && char === "\\") {
      escaping = true;
      continue;
    }

    if (quote) {
      if (char === quote) {
        quote = undefined;
      } else {
        current += char;
      }
      continue;
    }

    if (char === "'" || char === "\"") {
      quote = char;
      continue;
    }

    if (/\s/.test(char)) {
      if (current.length > 0) {
        tokens.push(current);
        current = "";
      }
      continue;
    }

    current += char;
  }

  if (escaping) {
    current += "\\";
  }

  if (quote) {
    throw new InvalidInputError(
      "preflight",
      "The configured CLI command could not be parsed.",
      "The `llmLogparser.cliCommand` setting has an unterminated quote or invalid escaping.",
      "Fix `llmLogparser.cliCommand` in settings and run the command again."
    );
  }

  if (current.length > 0) {
    tokens.push(current);
  }

  return tokens;
};

const formatArg = (value: string): string => {
  if (value.length === 0) {
    return '""';
  }
  if (/^[A-Za-z0-9_./:=+-]+$/.test(value)) {
    return value;
  }
  return `"${value.replace(/(["\\$`])/g, "\\$1")}"`;
};

export const getInvalidCliFields = (payload: CliRunPayload): string[] => {
  const missing: string[] = [];
  const opts = payload.options;

  if (payload.command === "parse") {
    if (!valueAsString(opts.provider)) missing.push("provider");
    if (!valueAsString(opts.input)) missing.push("input");
  } else if (payload.command === "export") {
    if (!valueAsString(opts.input)) missing.push("input");
  } else if (payload.command === "chain") {
    if (!valueAsString(opts.provider)) missing.push("provider");
    if (!valueAsString(opts.input)) missing.push("input");
  } else if (payload.command === "analyze") {
    if (!valueAsString(opts.analyzeCommand)) missing.push("analyzeCommand");
    if (!valueAsString(opts.input)) missing.push("input");
  }

  return missing;
};

const formatMissingFields = (fields: string[]): string => fields.join(", ");

const probeCommandAvailability = (
  command: string,
  cwd: string
): Promise<CommandProbeResult> => {
  const cacheKey = `${cwd}::${command}`;
  const cached = commandAvailabilityCache.get(cacheKey);
  if (cached) {
    return cached;
  }

  const pending = new Promise<CommandProbeResult>((resolve) => {
    const child = spawn(command, ["--version"], {
      cwd,
      stdio: "ignore",
    });

    let settled = false;

    const finish = (result: CommandProbeResult) => {
      if (settled) {
        return;
      }
      settled = true;
      resolve(result);
    };

    child.once("spawn", () => {
      finish("ok");
      child.kill();
    });

    child.once("error", (error: NodeJS.ErrnoException) => {
      if (error.code === "EACCES" || error.code === "EPERM") {
        finish("permissionDenied");
        return;
      }
      finish("missing");
    });
  });

  commandAvailabilityCache.set(cacheKey, pending);
  return pending;
};

const resolveConfiguredCommand = (cliCommand: string): string => {
  const [command] = tokenizeCommand(cliCommand);
  if (!command) {
    throw new InvalidInputError(
      "preflight",
      "The configured CLI command is empty.",
      "The `llmLogparser.cliCommand` setting does not contain an executable to run.",
      "Set `llmLogparser.cliCommand` to a valid command or clear it to use automatic detection."
    );
  }
  return command;
};

const createBinaryNotFoundError = (
  phase: ErrorPhase,
  strategy: CliLaunchStrategy,
  command: string
): BinaryNotFoundError => {
  if (strategy === "cliCommand") {
    return new BinaryNotFoundError(
      phase,
      "The configured CLI command is not available.",
      `The executable "${command}" from \`llmLogparser.cliCommand\` could not be found from this workspace.`,
      "Fix `llmLogparser.cliCommand` in settings or install the required tool, then run the command again."
    );
  }

  if (strategy === "uv") {
    return new BinaryNotFoundError(
      phase,
      "uv is not available.",
      "The workspace has a `pyproject.toml`, so the extension prefers `uv run llp`, but `uv` could not be started.",
      "Install uv or set `llmLogparser.cliCommand` in settings to a working command."
    );
  }

  return new BinaryNotFoundError(
    phase,
    `Python executable "${command}" is not available.`,
    "The extension fell back to `python -m llm_logparser.cli`, but the configured Python executable could not be found.",
    "Install Python, fix `llmLogparser.pythonPath`, or set `llmLogparser.cliCommand` in settings."
  );
};

const createPermissionDeniedError = (
  phase: ErrorPhase,
  strategy: CliLaunchStrategy,
  command: string
): PermissionDeniedError => {
  if (strategy === "cliCommand") {
    return new PermissionDeniedError(
      phase,
      "The configured CLI command could not be started.",
      `The operating system denied permission to run "${command}" from \`llmLogparser.cliCommand\`.`,
      "Check that the command is executable and accessible from this workspace, or update the setting."
    );
  }

  if (strategy === "uv") {
    return new PermissionDeniedError(
      phase,
      "uv could not be started.",
      `The operating system denied permission to run "${command}".`,
      "Check the uv installation and permissions, or set `llmLogparser.cliCommand` to another working command."
    );
  }

  return new PermissionDeniedError(
    phase,
    "Python could not be started.",
    `The operating system denied permission to run "${command}".`,
    "Check the Python executable path and permissions, or set `llmLogparser.cliCommand` to another working command."
  );
};

const ensureCommandAvailable = async (
  command: string,
  workspaceRoot: string,
  strategy: CliLaunchStrategy,
  phase: ErrorPhase
): Promise<void> => {
  const result = await probeCommandAvailability(command, workspaceRoot);

  if (result === "missing") {
    throw createBinaryNotFoundError(phase, strategy, command);
  }

  if (result === "permissionDenied") {
    throw createPermissionDeniedError(phase, strategy, command);
  }
};

const resolveLaunchStrategy = async (
  options: RunCliOptions,
  workspaceRoot: string
): Promise<CliLaunchStrategy> => {
  if (options.cliCommand && options.cliCommand.trim().length > 0) {
    return "cliCommand";
  }

  const [hasPyproject, hasUv] = await Promise.all([
    hasWorkspacePyproject(workspaceRoot),
    probeCommandAvailability("uv", workspaceRoot).then((result) => result === "ok"),
  ]);

  if (hasPyproject && hasUv) {
    return "uv";
  }

  return "pythonModule";
};

const buildCliInvocation = async (
  request: RunCliRequest,
  options: RunCliOptions,
  workspaceRoot: string
): Promise<ResolvedCliInvocation> => {
  const cliArgs = [request.command, ...request.args];
  const env = buildBaseEnv(options, workspaceRoot);
  const strategy = await resolveLaunchStrategy(options, workspaceRoot);
  const cliCommand = options.cliCommand?.trim();

  if (strategy === "cliCommand") {
    const [command, ...commandArgs] = tokenizeCommand(cliCommand ?? "");
    if (!command) {
      throw new InvalidInputError(
        "preflight",
        "The configured CLI command is empty.",
        "The `llmLogparser.cliCommand` setting does not contain an executable to run.",
        "Set `llmLogparser.cliCommand` to a valid command or clear it to use automatic detection."
      );
    }
    return {
      command,
      args: [...commandArgs, ...cliArgs],
      env,
      strategy,
    };
  }

  if (strategy === "uv") {
    return {
      command: "uv",
      args: ["run", "llp", ...cliArgs],
      env,
      strategy,
    };
  }

  return {
    command: options.pythonPath,
    args: ["-m", "llm_logparser.cli", ...cliArgs],
    env,
    strategy,
  };
};

export const createRunCliRequest = (payload: CliRunPayload): RunCliRequest => {
  const missing = getInvalidCliFields(payload);
  if (missing.length > 0) {
    throw new InvalidInputError(
      "preflight",
      "Required command inputs are missing.",
      `The ${payload.command} command needs these fields before it can run: ${formatMissingFields(
        missing
      )}.`,
      `Fill in ${formatMissingFields(missing)} in the panel and run the command again.`
    );
  }

  return {
    command: payload.command,
    args: buildCliArgs(payload),
  };
};

export const preflightCliExecution = async (
  request: RunCliRequest,
  options: RunCliOptions
): Promise<ResolvedCliInvocation> => {
  const workspaceRoot = await ensureWorkspaceRoot(options.cwd);
  const invocation = await buildCliInvocation(request, options, workspaceRoot);

  if (invocation.strategy === "cliCommand") {
    const configuredCommand = resolveConfiguredCommand(options.cliCommand?.trim() ?? "");
    await ensureCommandAvailable(configuredCommand, workspaceRoot, "cliCommand", "preflight");
    return invocation;
  }

  if (invocation.strategy === "uv") {
    const pyprojectExists = await hasWorkspacePyproject(workspaceRoot);
    if (!pyprojectExists) {
      throw new InvalidInputError(
        "preflight",
        "The workspace is not configured for uv execution.",
        "Automatic `uv run llp` execution requires a `pyproject.toml` at the workspace root.",
        "Open the repository root, add `pyproject.toml`, or set `llmLogparser.cliCommand` explicitly."
      );
    }
    await ensureCommandAvailable("uv", workspaceRoot, "uv", "preflight");
    return invocation;
  }

  await ensureCommandAvailable(options.pythonPath, workspaceRoot, "pythonModule", "preflight");
  return invocation;
};

const mapSpawnError = (
  error: unknown,
  invocation: ResolvedCliInvocation
): CliExecutionError => {
  const code =
    typeof error === "object" && error !== null && "code" in error
      ? String((error as NodeJS.ErrnoException).code)
      : undefined;

  if (code === "ENOENT") {
    return createBinaryNotFoundError("runtime", invocation.strategy, invocation.command);
  }

  if (code === "EACCES" || code === "EPERM") {
    return createPermissionDeniedError("runtime", invocation.strategy, invocation.command);
  }

  return new UnknownExecutionError(
    "runtime",
    "The command could not be started.",
    "The operating system returned an unexpected execution error before the CLI could run.",
    "Check the command configuration, then run the command again."
  );
};

const mapExitFailure = (
  exitCode: number | null,
  invocation: ResolvedCliInvocation
): CliExecutionError => {
  if (exitCode === 126) {
    return createPermissionDeniedError("runtime", invocation.strategy, invocation.command);
  }

  if (exitCode === 127) {
    return createBinaryNotFoundError("runtime", invocation.strategy, invocation.command);
  }

  return new UnknownExecutionError(
    "runtime",
    `The command exited with code ${exitCode ?? "unknown"}.`,
    "The CLI started, but it reported a failure. The command output above usually contains the exact reason.",
    "Review the command output, fix the reported problem, and run the command again."
  );
};

export const toCliUiError = (error: unknown): CliUiError => {
  if (error instanceof CliExecutionError) {
    return {
      type: error.type,
      what: error.what,
      why: error.why,
      nextStep: error.nextStep,
    };
  }

  return {
    type: "UnknownExecutionError",
    what: "The command failed for an unexpected reason.",
    why: "The extension received an error it could not classify safely.",
    nextStep: "Review the command output and settings, then try again.",
  };
};

export const formatCliCommandLine = async (
  request: RunCliRequest,
  options: RunCliOptions
): Promise<string> => {
  const invocation = await preflightCliExecution(request, options);
  return [invocation.command, ...invocation.args].map(formatArg).join(" ");
};

export const runCli = async (
  request: RunCliRequest,
  options: RunCliOptions
): Promise<number> => {
  const invocation = await preflightCliExecution(request, options);
  const workspaceRoot = await ensureWorkspaceRoot(options.cwd);

  return new Promise((resolve, reject) => {
    const child = spawn(invocation.command, invocation.args, {
      cwd: workspaceRoot,
      env: invocation.env,
    });

    child.stdout.on("data", (chunk: Buffer) => {
      options.onStdout?.(chunk.toString());
    });

    child.stderr.on("data", (chunk: Buffer) => {
      options.onStderr?.(chunk.toString());
    });

    child.on("error", (error) => reject(mapSpawnError(error, invocation)));
    child.on("close", (code) => {
      if (code === 0) {
        resolve(0);
        return;
      }
      reject(mapExitFailure(code, invocation));
    });
  });
};
