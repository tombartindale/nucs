// Running one bcn command for a job: the argv a job's command/args/target become, and the
// child process with its NDJSON stderr and single JSON envelope on stdout. Shared by the
// local worker (scheduler.ts) and a remote runner (runner.ts), so a job runs the same way
// wherever it is picked up.
import { spawn, type ChildProcess } from 'node:child_process';
import { constants } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import type { AnyEnvelope, BcnEvent, JobArgs } from '@beacon/shared';
import { FLAG_ARGS, VALUE_ORDER } from './jobs.js';

/** bcn's arguments for one target of a job, with the target resolved under `root`. */
export function bcnArgv(command: string, args: JobArgs, target: string, root: string): string[] {
  // docedit's job target is the specific document (e.g. "KV7016/course-map.md"), not the
  // module -- see the comment at its submit() call in server.ts -- so the module directory
  // bcn docedit actually takes positionally is only the target's first path segment here.
  const resolved = command === 'docedit' ? target.split('/')[0] : target;
  const path = resolved !== '.' ? join(root, resolved) : root;
  const argv = [command, path];
  if (command === 'media' && args.action) argv.push(String(args.action));  // bcn media <path> <action>
  for (const k of VALUE_ORDER) {
    if (k in args && args[k] !== null && args[k] !== '') argv.push(`--${k.replace(/_/g, '-')}`, String(args[k]));
  }
  const only = Array.isArray(args.only) ? args.only : args.only ? [args.only] : [];
  for (const p of only) argv.push('--only', String(p));
  const layout = Array.isArray(args.layout) ? args.layout : args.layout ? [args.layout] : [];
  for (const l of layout) argv.push('--layout', String(l));
  if ('import' in args) argv.push('--import', String(args.import));
  for (const k of FLAG_ARGS) if (args[k]) argv.push(`--${k.replace(/_/g, '-')}`);
  return argv;
}

/** A stderr line as an event; anything that is not NDJSON becomes an info log line. */
export function parseEvent(line: string): BcnEvent {
  try { return JSON.parse(line); } catch { return { event: 'log', level: 'info', message: line }; }
}

export interface Invocation {
  proc: ChildProcess;
  /** Resolves with the exit code (negative signal number if killed) and the envelope, if bcn printed one. */
  done: Promise<[number, AnyEnvelope | null]>;
}

/** Spawns `prefix… argv` and reports each non-empty stderr line as it arrives. */
export function invokeBcn(prefix: string[], argv: string[], opts: {
  cwd: string; env?: NodeJS.ProcessEnv; onLine: (line: string) => void; onNoEnvelope?: () => void;
}): Invocation {
  const [file, ...prefixRest] = prefix;
  const proc = spawn(file, [...prefixRest, ...argv], { cwd: opts.cwd, env: opts.env, stdio: ['ignore', 'pipe', 'pipe'] });
  const done = new Promise<[number, AnyEnvelope | null]>((resolve, reject) => {
    const out: Buffer[] = [];
    proc.stdout!.on('data', (b: Buffer) => out.push(b));
    createInterface({ input: proc.stderr! }).on('line', (line) => { if (line) opts.onLine(line); });
    proc.on('error', reject);
    proc.on('close', (code, signal) => {
      const text = Buffer.concat(out).toString('utf8');
      let env: AnyEnvelope | null = null;
      if (text.trim()) {
        try { env = JSON.parse(text); } catch { opts.onNoEnvelope?.(); }
      }
      resolve([code ?? (signal ? -(constants.signals[signal] ?? 1) : -1), env]);
    });
  });
  return { proc, done };
}
