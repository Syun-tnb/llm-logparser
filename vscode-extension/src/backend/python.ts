import * as path from "path";
import { spawn } from "child_process";

export type CliCommand = "parse" | "export" | "chain";
export type CliOptionValue = string | boolean | undefined;
export type CliOptions = Record<string, CliOptionValue>;

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

const PATH_SEPARATOR = process.platform === "win32" ? ";" : ":";

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

    if (char === "\\") {
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
    throw new Error(`Unterminated quote in cliCommand: ${commandLine}`);
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

export const validateCliPayload = (payload: CliRunPayload): string[] => {
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
  }

  return missing;
};

export const createRunCliRequest = (payload: CliRunPayload): RunCliRequest => ({
  command: payload.command,
  args: buildCliArgs(payload),
});

export const buildCliInvocation = (
  request: RunCliRequest,
  options: RunCliOptions
): { command: string; args: string[]; env: NodeJS.ProcessEnv } => {
  const cliArgs = [request.command, ...request.args];
  const env = { ...process.env, ...options.env };
  const pythonPath = path.join(options.cwd, "src");
  env.PYTHONPATH = appendPath(env.PYTHONPATH, pythonPath);

  if (options.cliCommand && options.cliCommand.trim().length > 0) {
    const [command, ...commandArgs] = tokenizeCommand(options.cliCommand);
    if (!command) {
      throw new Error("cliCommand must include an executable.");
    }
    return {
      command,
      args: [...commandArgs, ...cliArgs],
      env,
    };
  }

  return {
    command: options.pythonPath,
    args: ["-m", "llm_logparser.cli", ...cliArgs],
    env,
  };
};

export const formatCliCommandLine = (
  request: RunCliRequest,
  options: RunCliOptions
): string => {
  const invocation = buildCliInvocation(request, options);
  return [invocation.command, ...invocation.args].map(formatArg).join(" ");
};

export const runCli = (
  request: RunCliRequest,
  options: RunCliOptions
): Promise<number> => {
  const invocation = buildCliInvocation(request, options);

  return new Promise((resolve, reject) => {
    const child = spawn(invocation.command, invocation.args, {
      cwd: options.cwd,
      env: invocation.env,
    });

    child.stdout.on("data", (chunk: Buffer) => {
      options.onStdout?.(chunk.toString());
    });

    child.stderr.on("data", (chunk: Buffer) => {
      options.onStderr?.(chunk.toString());
    });

    child.on("error", (err) => reject(err));
    child.on("close", (code) => resolve(code ?? 1));
  });
};
