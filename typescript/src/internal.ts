import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";

import { firefox, type BrowserContext } from "playwright-core";

type PersistentContextOptions = NonNullable<Parameters<typeof firefox.launchPersistentContext>[1]>;
type ProxySettings = NonNullable<PersistentContextOptions["proxy"]>;

export interface PreparedCleanup {
  version: 1;
  nonce: string;
  metadataPath: string;
  profileDir: string;
  removeProfile: boolean;
  sessionToken: string;
  virtualDisplayPid?: number;
}

export interface PreparedLaunch {
  seed: number;
  executablePath: string;
  profileDir: string;
  headless: boolean;
  args: string[];
  env: Record<string, string>;
  proxy?: ProxySettings;
  context: Pick<PersistentContextOptions, "viewport" | "screen" | "locale" | "timezoneId">;
  cleanup: PreparedCleanup;
}

export interface BridgeHandle {
  prepared: PreparedLaunch;
  close(): Promise<void>;
}

export interface ContextLike {
  close(): Promise<void>;
  on(event: "close", listener: () => void): unknown;
}

export interface BrowserTypeLike {
  launchPersistentContext(
    profileDir: string,
    options: PersistentContextOptions,
  ): Promise<ContextLike>;
}

export type BridgeFactory<Options> = (options: Options) => Promise<BridgeHandle>;

function incompatible(reason: string): never {
  throw new Error(`incompatible Python bridge response: ${reason}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireExactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
  label = "response",
): void {
  const allowed = new Set([...required, ...optional]);
  const actual = Object.keys(value);
  const missing = required.filter(key => !Object.hasOwn(value, key));
  const extra = actual.filter(key => !allowed.has(key));
  if (missing.length || extra.length) {
    incompatible(`${label} has invalid keys`);
  }
}

function positiveInteger(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) &&
    Number.isInteger(value) && value > 0;
}

function validateDimensions(value: unknown, label: string): void {
  if (!isRecord(value)) incompatible(`${label} must be an object`);
  requireExactKeys(value, ["width", "height"], [], label);
  if (!positiveInteger(value.width) || !positiveInteger(value.height)) {
    incompatible(`${label} dimensions must be positive finite integers`);
  }
}

function validatePreparedLaunch(value: unknown): PreparedLaunch {
  if (!isRecord(value)) incompatible("response must be an object");
  requireExactKeys(
    value,
    ["seed", "executablePath", "profileDir", "headless", "args", "env", "context", "cleanup"],
    ["proxy"],
  );
  if (typeof value.seed !== "number" || !Number.isFinite(value.seed) ||
      !Number.isInteger(value.seed)) incompatible("seed must be a finite integer");
  for (const field of ["executablePath", "profileDir"] as const) {
    if (typeof value[field] !== "string" || value[field].length === 0) {
      incompatible(`${field} must be a nonempty string`);
    }
  }
  if (typeof value.headless !== "boolean") incompatible("headless must be a boolean");
  if (!Array.isArray(value.args) || !value.args.every(arg => typeof arg === "string")) {
    incompatible("args must be an array of strings");
  }
  if (!isRecord(value.env) ||
      !Object.entries(value.env).every(([key, entry]) => key.length > 0 && typeof entry === "string")) {
    incompatible("env must contain only nonempty string keys and string values");
  }
  if (Object.hasOwn(value, "proxy")) {
    const proxy = value.proxy;
    if (!isRecord(proxy)) incompatible("proxy must be an object when present");
    requireExactKeys(proxy, ["server"], ["username", "password", "bypass"], "proxy");
    if (typeof proxy.server !== "string" || proxy.server.length === 0) {
      incompatible("proxy.server must be a nonempty string");
    }
    for (const field of ["username", "password", "bypass"] as const) {
      if (Object.hasOwn(proxy, field) && typeof proxy[field] !== "string") {
        incompatible(`proxy.${field} must be a string`);
      }
    }
  }
  const context = value.context;
  if (!isRecord(context)) incompatible("context must be an object");
  requireExactKeys(context, ["viewport", "screen"], ["locale", "timezoneId"], "context");
  validateDimensions(context.viewport, "context.viewport");
  validateDimensions(context.screen, "context.screen");
  for (const field of ["locale", "timezoneId"] as const) {
    if (Object.hasOwn(context, field) && typeof context[field] !== "string") {
      incompatible(`context.${field} must be a string`);
    }
  }
  const cleanup = value.cleanup;
  if (!isRecord(cleanup)) incompatible("cleanup must be an object");
  requireExactKeys(
    cleanup,
    ["version", "nonce", "metadataPath", "profileDir", "removeProfile", "sessionToken"],
    ["virtualDisplayPid"],
    "cleanup",
  );
  if (cleanup.version !== 1) incompatible("cleanup.version must be 1");
  if (typeof cleanup.nonce !== "string" || !/^[0-9a-f]{64}$/.test(cleanup.nonce)) {
    incompatible("cleanup.nonce is invalid");
  }
  for (const field of ["metadataPath", "profileDir"] as const) {
    if (typeof cleanup[field] !== "string" || !path.isAbsolute(cleanup[field])) {
      incompatible(`cleanup.${field} must be an absolute path`);
    }
  }
  if (cleanup.profileDir !== value.profileDir) {
    incompatible("cleanup.profileDir does not match profileDir");
  }
  if (typeof cleanup.removeProfile !== "boolean") {
    incompatible("cleanup.removeProfile must be a boolean");
  }
  if (typeof cleanup.sessionToken !== "string" ||
      !/^[0-9a-f]{32}$/.test(cleanup.sessionToken)) {
    incompatible("cleanup.sessionToken is invalid");
  }
  if (Object.hasOwn(cleanup, "virtualDisplayPid") &&
      !positiveInteger(cleanup.virtualDisplayPid)) {
    incompatible("cleanup.virtualDisplayPid must be a positive finite integer");
  }
  return value as unknown as PreparedLaunch;
}

export function validatePreparedLaunchForTest(value: unknown): PreparedLaunch {
  return validatePreparedLaunch(value);
}

function validateSerializableOptions(options: { seed?: number }): void {
  if (options.seed !== undefined &&
      (!Number.isFinite(options.seed) || !Number.isInteger(options.seed))) {
    throw new TypeError("seed must be a finite integer");
  }
}

const START_TIMEOUT_MS = 120_000;
const CLOSE_TIMEOUT_MS = 5_000;
const FORCE_KILL_TIMEOUT_MS = 1_000;
const EMERGENCY_CLEANUP_TIMEOUT_MS = 10_000;
const CLEANUP_PATH_VAR = "INVPW_TYPESCRIPT_CLEANUP_PATH";
const CLEANUP_NONCE_VAR = "INVPW_TYPESCRIPT_CLEANUP_NONCE";

interface ExitWaitChild {
  exitCode: number | null;
  signalCode: NodeJS.Signals | null;
  kill(signal?: NodeJS.Signals | number): boolean;
  once(event: "exit", listener: () => void): unknown;
  removeListener(event: "exit", listener: () => void): unknown;
}

function waitForExit(
  child: ExitWaitChild,
  timeoutMs: number,
  forceKillTimeoutMs = FORCE_KILL_TIMEOUT_MS,
  finalTimeoutMs = FORCE_KILL_TIMEOUT_MS,
): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let forceTimer: NodeJS.Timeout | undefined;
    let finalTimer: NodeJS.Timeout | undefined;
    const finish = (callback: () => void) => {
      clearTimeout(timer);
      if (forceTimer) clearTimeout(forceTimer);
      if (finalTimer) clearTimeout(finalTimer);
      child.removeListener("exit", onExit);
      callback();
    };
    const onExit = () => finish(resolve);
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      forceTimer = setTimeout(() => {
        if (child.exitCode !== null || child.signalCode !== null) return;
        child.kill("SIGKILL");
        finalTimer = setTimeout(() => {
          if (child.exitCode === null && child.signalCode === null) {
            finish(() => reject(new Error("bridge process did not exit after SIGKILL")));
          }
        }, finalTimeoutMs);
      }, forceKillTimeoutMs);
    }, timeoutMs);
    child.once("exit", onExit);
  });
}

export function waitForExitForTest(
  child: ExitWaitChild,
  timeoutMs: number,
  forceKillTimeoutMs: number,
  finalTimeoutMs: number,
): Promise<void> {
  return waitForExit(child, timeoutMs, forceKillTimeoutMs, finalTimeoutMs);
}

interface CleanupOwner {
  directory: string;
  metadataPath: string;
  nonce: string;
  profileDir: string;
  removeProfile: boolean;
}

function resolveProfilePath(profileDir: string): string {
  const expanded = profileDir === "~"
    ? homedir()
    : profileDir.startsWith(`~${path.sep}`)
      ? path.join(homedir(), profileDir.slice(2))
      : profileDir;
  return path.resolve(expanded);
}

async function createCleanupOwner(bridgeOptions: Record<string, unknown>): Promise<CleanupOwner> {
  const directory = await mkdtemp(path.join(tmpdir(), "invisible-playwright-node-owner-"));
  const metadataPath = path.join(directory, "cleanup.json");
  const supplied = typeof bridgeOptions.profileDir === "string" && bridgeOptions.profileDir.length > 0;
  const profileDir = supplied
    ? resolveProfilePath(bridgeOptions.profileDir as string)
    : path.join(directory, "profile");
  const owner = {
    directory,
    metadataPath,
    nonce: randomBytes(32).toString("hex"),
    profileDir,
    removeProfile: !supplied,
  };
  await writeFile(metadataPath, JSON.stringify({
    version: 1,
    nonce: owner.nonce,
    profileDir,
    removeProfile: owner.removeProfile,
    sessionToken: "",
    virtualDisplayPid: null,
  }), { encoding: "utf8", mode: 0o600 });
  return owner;
}

function validateCleanupOwnership(prepared: PreparedLaunch, owner: CleanupOwner): void {
  const cleanup = prepared.cleanup;
  if (cleanup.nonce !== owner.nonce || cleanup.metadataPath !== owner.metadataPath ||
      cleanup.removeProfile !== owner.removeProfile) {
    incompatible("cleanup identity does not match the Node owner");
  }
  if (owner.removeProfile && cleanup.profileDir !== owner.profileDir) {
    incompatible("ephemeral cleanup profile does not match the Node owner");
  }
}

async function runEmergencyCleanup(
  python: string,
  owner: CleanupOwner,
  baseEnv: NodeJS.ProcessEnv,
): Promise<void> {
  const cleanup = spawn(
    python,
    ["-m", "invisible_playwright._typescript_bridge", "--cleanup", owner.metadataPath, owner.nonce],
    { env: baseEnv, stdio: ["ignore", "ignore", "pipe"], windowsHide: true },
  );
  let stderr = "";
  cleanup.stderr.setEncoding("utf8");
  cleanup.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(-16_384); });
  const spawnFailure = new Promise<never>((_resolve, reject) => {
    cleanup.once("error", reject);
  });
  await Promise.race([
    waitForExit(cleanup, EMERGENCY_CLEANUP_TIMEOUT_MS),
    spawnFailure,
  ]);
  if (cleanup.exitCode !== 0) {
    const status = cleanup.signalCode
      ? `terminated by ${cleanup.signalCode}`
      : `exited with code ${cleanup.exitCode}`;
    throw new Error(
      `emergency cleanup could not be confirmed: helper ${status}` +
      `${stderr ? `: ${stderr.trim()}` : ""}`,
    );
  }
  await rm(owner.directory, { recursive: true, force: true });
}

async function combineCleanupFailure(original: unknown, cleanup: Promise<void>): Promise<never> {
  try {
    await cleanup;
  } catch (cleanupFailure) {
    throw new AggregateError(
      [original, cleanupFailure],
      "Python bridge failed and emergency cleanup could not be confirmed",
    );
  }
  throw original;
}

async function startPythonBridgeWithTimeout<Options extends { pythonExecutable?: string; seed?: number }>(
  options: Options,
  startTimeoutMs: number,
): Promise<BridgeHandle> {
  validateSerializableOptions(options);
  const { pythonExecutable, ...bridgeOptions } = options;
  const python = pythonExecutable || process.env.INVPW_PYTHON ||
    (process.platform === "win32" ? "python" : "python3");
  const owner = await createCleanupOwner(bridgeOptions as Record<string, unknown>);
  const bridgeEnv = {
    ...process.env,
    [CLEANUP_PATH_VAR]: owner.metadataPath,
    [CLEANUP_NONCE_VAR]: owner.nonce,
  };
  const child = spawn(python, ["-m", "invisible_playwright._typescript_bridge"], {
    env: bridgeEnv,
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });

  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(-16_384); });

  try {
    const prepared = await new Promise<PreparedLaunch>((resolve, reject) => {
      let stdout = "";
      const timer = setTimeout(() => {
        reject(new Error(`invisible_playwright Python bridge timed out after ${startTimeoutMs} ms`));
      }, startTimeoutMs);
      timer.unref();

      const finish = (callback: () => void) => {
        clearTimeout(timer);
        child.stdout.removeAllListeners("data");
        child.removeListener("error", onError);
        child.removeListener("exit", onExit);
        callback();
      };
      const onError = (error: Error) => finish(() => reject(new Error(
        `could not start ${python}: ${error.message}`,
        { cause: error },
      )));
      const onExit = (code: number | null, signal: NodeJS.Signals | null) => finish(() => reject(new Error(
        `invisible_playwright Python bridge exited before launch preparation ` +
        `(code ${code}, signal ${signal})${stderr ? `: ${stderr.trim()}` : ""}`,
      )));

      child.once("error", onError);
      child.once("exit", onExit);
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", chunk => {
        stdout += chunk;
        const newline = stdout.indexOf("\n");
        if (newline === -1) return;
        const line = stdout.slice(0, newline);
        finish(() => {
          try {
            const response = validatePreparedLaunch(JSON.parse(line));
            validateCleanupOwnership(response, owner);
            resolve(response);
          } catch (error) {
            if (error instanceof SyntaxError) {
              reject(new Error("invisible_playwright Python bridge returned invalid JSON", { cause: error }));
            } else {
              reject(error);
            }
          }
        });
      });

      child.stdin.write(JSON.stringify(bridgeOptions) + "\n");
    });

    let closed = false;
    return {
      prepared,
      async close() {
        if (closed) return;
        closed = true;
        child.stdin.end();
        await waitForExit(child, CLOSE_TIMEOUT_MS);
        if (child.exitCode !== 0) {
          const status = child.signalCode
            ? `terminated by ${child.signalCode}`
            : `exited with code ${child.exitCode}`;
          const failure = new Error(
            `invisible_playwright Python bridge ${status}${stderr ? `: ${stderr.trim()}` : ""}`,
          );
          return combineCleanupFailure(
            failure,
            runEmergencyCleanup(python, owner, bridgeEnv),
          );
        }
        await rm(owner.directory, { recursive: true, force: true });
      },
    };
  } catch (error) {
    child.stdin.destroy();
    child.kill();
    let exitFailure: unknown;
    try {
      await waitForExit(child, CLOSE_TIMEOUT_MS);
    } catch (failure) {
      exitFailure = failure;
    }
    const original = exitFailure
      ? new AggregateError([error, exitFailure], "Python bridge startup and termination failed")
      : error;
    return combineCleanupFailure(
      original,
      runEmergencyCleanup(python, owner, bridgeEnv),
    );
  }
}

export function startPythonBridge<Options extends { pythonExecutable?: string; seed?: number }>(
  options: Options,
): Promise<BridgeHandle> {
  return startPythonBridgeWithTimeout(options, START_TIMEOUT_MS);
}

export function startPythonBridgeForTest<Options extends { pythonExecutable?: string; seed?: number }>(
  options: Options,
  startTimeoutMs: number,
): Promise<BridgeHandle> {
  return startPythonBridgeWithTimeout(options, startTimeoutMs);
}

export type PublicContext = BrowserContext;

export interface InvisiblePlaywrightOptions {
  seed?: number;
  pin?: Record<string, unknown>;
  headless?: boolean;
  proxy?: ProxySettings;
  extraArgs?: string[];
  humanize?: boolean | number;
  locale?: string;
  timezone?: string;
  extraPrefs?: Record<string, string | number | boolean>;
  binaryPath?: string;
  profileDir?: string;
  showCursor?: boolean;
  /** Python interpreter containing the matching invisible-playwright package. */
  pythonExecutable?: string;
}

function inheritedEnvironment(delta: Record<string, string>): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  return { ...env, ...delta };
}

async function withTimeout(
  operation: Promise<void>,
  timeoutMs: number,
  description: string,
): Promise<void> {
  let timer: NodeJS.Timeout | undefined;
  try {
    await Promise.race([
      operation,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          reject(new Error(`${description} timed out after ${timeoutMs} ms`));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export class InvisiblePlaywright {
  readonly options: Readonly<InvisiblePlaywrightOptions>;
  seed: number | undefined;

  private readonly browserType: BrowserTypeLike;
  private readonly bridgeFactory: BridgeFactory<InvisiblePlaywrightOptions>;
  private readonly closeTimeoutMs: number;
  private context: ContextLike | undefined;
  private bridge: BridgeHandle | undefined;
  private bridgeCloseStarted: Promise<void> | undefined;
  private launchStarted = false;
  private launchSettled: Promise<void> | undefined;
  private closeStarted: Promise<void> | undefined;

  constructor(options?: InvisiblePlaywrightOptions);
  constructor(
    options: InvisiblePlaywrightOptions = {},
    browserType: BrowserTypeLike = firefox,
    bridgeFactory: BridgeFactory<InvisiblePlaywrightOptions> = startPythonBridge,
    closeTimeoutMs = CLOSE_TIMEOUT_MS,
  ) {
    this.options = Object.freeze({ ...options });
    this.browserType = browserType;
    this.bridgeFactory = bridgeFactory;
    this.closeTimeoutMs = closeTimeoutMs;
    this.seed = options.seed;
  }

  async launch(): Promise<BrowserContext> {
    if (this.closeStarted) throw new Error("InvisiblePlaywright has already been closed");
    if (this.launchStarted) throw new Error("InvisiblePlaywright.launch() may only be called once");
    this.launchStarted = true;
    let markLaunchSettled!: () => void;
    this.launchSettled = new Promise<void>(resolve => { markLaunchSettled = resolve; });

    try {
      const bridge = await this.bridgeFactory({ ...this.options });
      this.bridge = bridge;
      this.seed = bridge.prepared.seed;
      const prepared = bridge.prepared;

      try {
        const launchOptions: PersistentContextOptions = {
          executablePath: prepared.executablePath,
          headless: prepared.headless,
          args: prepared.args,
          env: inheritedEnvironment(prepared.env),
          ...prepared.context,
        };
        if (prepared.proxy) launchOptions.proxy = prepared.proxy;
        const context = await this.browserType.launchPersistentContext(
          prepared.profileDir,
          launchOptions,
        );
        this.context = context;
        context.on("close", () => { void this.closeBridge().catch(() => {}); });
        return context as BrowserContext;
      } catch (error) {
        await this.closeBridge();
        throw error;
      }
    } finally {
      markLaunchSettled();
    }
  }

  private async closeBridge(): Promise<void> {
    if (this.bridgeCloseStarted) return this.bridgeCloseStarted;
    const bridge = this.bridge;
    this.bridge = undefined;
    this.bridgeCloseStarted = (async () => {
      if (bridge) await bridge.close();
    })();
    return this.bridgeCloseStarted;
  }

  async close(): Promise<void> {
    if (this.closeStarted) return this.closeStarted;
    this.closeStarted = (async () => {
      if (this.launchSettled) await this.launchSettled;
      const context = this.context;
      this.context = undefined;
      let contextFailure: unknown;
      try {
        if (context) {
          await withTimeout(
            context.close(),
            this.closeTimeoutMs,
            "Playwright context close",
          );
        }
      } catch (failure) {
        contextFailure = failure;
      }
      let bridgeFailure: unknown;
      try {
        await this.closeBridge();
      } catch (failure) {
        bridgeFailure = failure;
      }
      if (contextFailure && bridgeFailure) {
        throw new AggregateError(
          [contextFailure, bridgeFailure],
          "Playwright context and Python bridge cleanup both failed",
        );
      }
      if (contextFailure) throw contextFailure;
      if (bridgeFailure) throw bridgeFailure;
    })();
    return this.closeStarted;
  }
}

export function createInvisiblePlaywrightForTest(
  options: InvisiblePlaywrightOptions,
  browserType: BrowserTypeLike,
  bridgeFactory: BridgeFactory<InvisiblePlaywrightOptions>,
  closeTimeoutMs = CLOSE_TIMEOUT_MS,
): InvisiblePlaywright {
  return new (InvisiblePlaywright as unknown as new (
    options: InvisiblePlaywrightOptions,
    browserType: BrowserTypeLike,
    bridgeFactory: BridgeFactory<InvisiblePlaywrightOptions>,
    closeTimeoutMs: number,
  ) => InvisiblePlaywright)(options, browserType, bridgeFactory, closeTimeoutMs);
}
