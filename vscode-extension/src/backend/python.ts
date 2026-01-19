import * as path from "path";
import { spawn } from "child_process";

export type CliCommand = "parse" | "export" | "chain";

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

export const buildCliInvocation = (
  request: RunCliRequest,
  options: RunCliOptions
): { command: string; args: string[]; env: NodeJS.ProcessEnv } => {
  const cliArgs = [request.command, ...request.args];
  const env = { ...process.env, ...options.env };
  const pythonPath = path.join(options.cwd, "src");
  env.PYTHONPATH = appendPath(env.PYTHONPATH, pythonPath);

  if (options.cliCommand && options.cliCommand.trim().length > 0) {
    return {
      command: options.cliCommand,
      args: cliArgs,
      env,
    };
  }

  return {
    command: options.pythonPath,
    args: ["-m", "llm_logparser.cli", ...cliArgs],
    env,
  };
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
