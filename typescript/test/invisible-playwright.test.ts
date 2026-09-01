import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { EventEmitter } from "node:events";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import { InvisiblePlaywright } from "../src/index.js";
import {
  createInvisiblePlaywrightForTest,
  startPythonBridge,
  startPythonBridgeForTest,
  validatePreparedLaunchForTest,
  waitForExitForTest,
  type PreparedLaunch,
} from "../src/internal.js";

const execFileAsync = promisify(execFile);

const prepared: PreparedLaunch = {
  seed: 42,
  executablePath: "/patched/firefox",
  profileDir: "/tmp/profile",
  headless: true,
  args: ["--one"],
  env: { TEST_ENV: "yes" },
  proxy: { server: "http://proxy.test:8080" },
  context: {
    viewport: { width: 1920, height: 947 },
    screen: { width: 1920, height: 1080 },
    locale: "en-US",
    timezoneId: "UTC",
  },
  cleanup: {
    version: 1,
    nonce: "a".repeat(64),
    metadataPath: "/tmp/invisible-playwright-node-owner-test/cleanup.json",
    profileDir: "/tmp/profile",
    removeProfile: false,
    sessionToken: "b".repeat(32),
  },
};

class FakeContext extends EventEmitter {
  closeCalls = 0;
  async close(): Promise<void> {
    this.closeCalls += 1;
    this.emit("close");
  }
}

test("the public entry point is the InvisiblePlaywright class", () => {
  assert.equal(typeof InvisiblePlaywright, "function");
});

for (const seed of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
  test(`non-finite seed ${String(seed)} is rejected before starting Python`, async () => {
    await assert.rejects(
      startPythonBridge({ seed, pythonExecutable: "/definitely/not/a/python" }),
      /seed must be a finite integer/,
    );
  });
}

test("every prepared launch field is validated before Playwright sees it", () => {
  const invalid: Array<[string, unknown]> = [
    ["root shape", null],
    ["root keys", { ...prepared, surprise: true }],
    ["seed", { ...prepared, seed: 1.5 }],
    ["executablePath", { ...prepared, executablePath: "" }],
    ["profileDir", { ...prepared, profileDir: "" }],
    ["headless", { ...prepared, headless: "yes" }],
    ["args", { ...prepared, args: ["--ok", 7] }],
    ["env", { ...prepared, env: { OK: "yes", BAD: 7 } }],
    ["proxy shape", { ...prepared, proxy: null }],
    ["proxy server", { ...prepared, proxy: { server: "" } }],
    ["proxy keys", { ...prepared, proxy: { server: "http://proxy", extra: "no" } }],
    ["context keys", { ...prepared, context: { ...prepared.context, extra: true } }],
    ["viewport width", {
      ...prepared,
      context: { ...prepared.context, viewport: { width: 0, height: 947 } },
    }],
    ["screen height", {
      ...prepared,
      context: { ...prepared.context, screen: { width: 1920, height: Number.POSITIVE_INFINITY } },
    }],
    ["locale", { ...prepared, context: { ...prepared.context, locale: 7 } }],
    ["timezoneId", { ...prepared, context: { ...prepared.context, timezoneId: false } }],
    ["cleanup keys", { ...prepared, cleanup: { ...prepared.cleanup, extra: true } }],
    ["cleanup nonce", { ...prepared, cleanup: { ...prepared.cleanup, nonce: "bad" } }],
    ["cleanup metadataPath", {
      ...prepared,
      cleanup: { ...prepared.cleanup, metadataPath: "relative.json" },
    }],
    ["cleanup profile ownership", {
      ...prepared,
      cleanup: { ...prepared.cleanup, removeProfile: "yes" },
    }],
    ["cleanup token", { ...prepared, cleanup: { ...prepared.cleanup, sessionToken: "bad" } }],
    ["cleanup display pid", {
      ...prepared,
      cleanup: { ...prepared.cleanup, virtualDisplayPid: 0 },
    }],
  ];

  for (const [name, value] of invalid) {
    assert.throws(
      () => validatePreparedLaunchForTest(value),
      /incompatible Python bridge response/,
      name,
    );
  }
  assert.deepEqual(validatePreparedLaunchForTest(prepared), prepared);
});

test("npm metadata pins the runtime and requests public provenance", async () => {
  const packageJson = JSON.parse(await readFile("package.json", "utf8")) as {
    name: string;
    version: string;
    dependencies: Record<string, string>;
    publishConfig?: Record<string, unknown>;
    exports?: Record<string, unknown>;
    repository?: unknown;
    homepage?: string;
    bugs?: unknown;
  };

  assert.equal(packageJson.name, "invisible-playwright");
  assert.equal(packageJson.version, "0.8.3");
  assert.equal(packageJson.dependencies["playwright-core"], "1.61.0");
  assert.deepEqual(packageJson.publishConfig, { access: "public", provenance: true });
  assert.deepEqual(packageJson.exports, {
    ".": { types: "./dist/index.d.ts", import: "./dist/index.js" },
  });
  assert.deepEqual(packageJson.repository, {
    type: "git",
    url: "git+https://github.com/feder-cr/invisible_playwright.git",
  });
  assert.equal(packageJson.homepage, "https://github.com/feder-cr/invisible_playwright#readme");
  assert.deepEqual(packageJson.bugs, {
    url: "https://github.com/feder-cr/invisible_playwright/issues",
  });
});

test("the npm LICENSE exactly matches the repository MIT license", async () => {
  assert.equal(await readFile("LICENSE", "utf8"), await readFile("../LICENSE", "utf8"));
});

test("npm pack contains only the built API, readme, and metadata", async () => {
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  await execFileAsync(npm, ["run", "build"]);
  const { stdout } = await execFileAsync(npm, ["pack", "--dry-run", "--json"]);
  const packed = JSON.parse(stdout) as Array<{ files: Array<{ path: string }> }>;

  assert.deepEqual(
    packed[0].files.map(file => file.path).sort(),
    [
      "LICENSE",
      "README.md",
      "dist/index.d.ts",
      "dist/index.js",
      "dist/internal.d.ts",
      "dist/internal.js",
      "package.json",
    ],
  );
});

test("launch returns the ordinary Playwright context with prepared launch options", async () => {
  const context = new FakeContext();
  let received: unknown;
  const browserType = {
    async launchPersistentContext(profileDir: string, options: unknown) {
      received = { profileDir, options };
      return context;
    },
  };
  let bridgeClosed = 0;
  const session = createInvisiblePlaywrightForTest(
    { seed: 42, headless: true },
    browserType,
    async () => ({ prepared, close: async () => { bridgeClosed += 1; } }),
  );

  const launched = await session.launch();

  assert.equal(launched, context);
  assert.equal(session.seed, 42);
  assert.equal((received as { options: { env: Record<string, string> } }).options.env.TEST_ENV, "yes");
  const receivedWithoutInheritedEnv = structuredClone(received) as {
    profileDir: string;
    options: Record<string, unknown>;
  };
  delete receivedWithoutInheritedEnv.options.env;
  assert.deepEqual(receivedWithoutInheritedEnv, {
    profileDir: "/tmp/profile",
    options: {
      executablePath: "/patched/firefox",
      headless: true,
      args: ["--one"],
      proxy: { server: "http://proxy.test:8080" },
      viewport: { width: 1920, height: 947 },
      screen: { width: 1920, height: 1080 },
      locale: "en-US",
      timezoneId: "UTC",
    },
  });

  context.emit("close");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(bridgeClosed, 1);
});

test("launch omits an absent proxy instead of passing null to Playwright", async () => {
  const context = new FakeContext();
  let receivedOptions: Record<string, unknown> | undefined;
  const noProxy = { ...prepared, proxy: undefined };
  const session = createInvisiblePlaywrightForTest(
    {},
    {
      async launchPersistentContext(_profileDir, options) {
        receivedOptions = options as Record<string, unknown>;
        return context;
      },
    },
    async () => ({ prepared: noProxy, close: async () => {} }),
  );

  await session.launch();

  assert.equal(Object.hasOwn(receivedOptions!, "proxy"), false);
  await session.close();
});

test("close shuts down both the Playwright context and Python bridge once", async () => {
  const context = new FakeContext();
  let bridgeClosed = 0;
  const session = createInvisiblePlaywrightForTest(
    {},
    { async launchPersistentContext() { return context; } },
    async () => ({ prepared, close: async () => { bridgeClosed += 1; } }),
  );

  await session.launch();
  await session.close();
  await session.close();

  assert.equal(context.closeCalls, 1);
  assert.equal(bridgeClosed, 1);
});

test("a failed browser launch does not leave the bridge alive", async () => {
  let bridgeClosed = 0;
  const session = createInvisiblePlaywrightForTest(
    {},
    { async launchPersistentContext() { throw new Error("launch failed"); } },
    async () => ({ prepared, close: async () => { bridgeClosed += 1; } }),
  );

  await assert.rejects(session.launch(), /launch failed/);
  assert.equal(bridgeClosed, 1);
});

test("launch is rejected after close has completed", async () => {
  let bridgeStarts = 0;
  const session = createInvisiblePlaywrightForTest(
    {},
    { async launchPersistentContext() { return new FakeContext(); } },
    async () => {
      bridgeStarts += 1;
      return { prepared, close: async () => {} };
    },
  );

  await session.close();

  await assert.rejects(session.launch(), /closed/);
  assert.equal(bridgeStarts, 0);
});

test("close waits for an in-flight launch and cleans up its late context", async () => {
  const context = new FakeContext();
  let resolveContext!: (context: FakeContext) => void;
  let markLaunchEntered!: () => void;
  const launchEntered = new Promise<void>(resolve => { markLaunchEntered = resolve; });
  const session = createInvisiblePlaywrightForTest(
    {},
    {
      async launchPersistentContext() {
        markLaunchEntered();
        return new Promise<FakeContext>(resolve => { resolveContext = resolve; });
      },
    },
    async () => ({ prepared, close: async () => {} }),
  );

  const launchPromise = session.launch();
  await launchEntered;
  let closeSettled = false;
  const closePromise = session.close().then(() => { closeSettled = true; });
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(closeSettled, false);
  resolveContext(context);
  await launchPromise;
  await closePromise;
  assert.equal(context.closeCalls, 1);
});

test("explicit close awaits bridge cleanup started by the context close event", async () => {
  const context = new FakeContext();
  let finishBridgeClose!: () => void;
  let markBridgeCloseStarted!: () => void;
  const bridgeCloseStarted = new Promise<void>(resolve => { markBridgeCloseStarted = resolve; });
  const session = createInvisiblePlaywrightForTest(
    {},
    { async launchPersistentContext() { return context; } },
    async () => ({
      prepared,
      close: async () => {
        markBridgeCloseStarted();
        await new Promise<void>(resolve => { finishBridgeClose = resolve; });
      },
    }),
  );
  await session.launch();

  context.emit("close");
  await bridgeCloseStarted;
  let closeSettled = false;
  const closePromise = session.close().then(() => { closeSettled = true; });
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(closeSettled, false);
  finishBridgeClose();
  await closePromise;
});

test("context close cleanup rejection is handled until explicit close observes it", async () => {
  const context = new FakeContext();
  const unhandled: unknown[] = [];
  const onUnhandled = (failure: unknown) => { unhandled.push(failure); };
  process.on("unhandledRejection", onUnhandled);
  const session = createInvisiblePlaywrightForTest(
    {},
    { async launchPersistentContext() { return context; } },
    async () => ({
      prepared,
      close: async () => { throw new Error("reaper failed"); },
    }),
  );

  try {
    await session.launch();
    context.emit("close");
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(unhandled, []);
    await assert.rejects(session.close(), /reaper failed/);
  } finally {
    process.removeListener("unhandledRejection", onUnhandled);
  }
});

test("explicit close bounds a stuck context and still reports failed bridge cleanup", async () => {
  let bridgeCloseCalls = 0;
  const session = createInvisiblePlaywrightForTest(
    {},
    {
      async launchPersistentContext() {
        return {
          close: async () => new Promise<void>(() => {}),
          on() {},
        };
      },
    },
    async () => ({
      prepared,
      close: async () => {
        bridgeCloseCalls += 1;
        throw new Error("bridge cleanup failed");
      },
    }),
    10,
  );
  await session.launch();

  const failure = await session.close().then(
    () => undefined,
    error => error as AggregateError,
  );

  assert(failure instanceof AggregateError);
  assert.deepEqual(
    failure.errors.map(error => String(error)),
    [
      "Error: Playwright context close timed out after 10 ms",
      "Error: bridge cleanup failed",
    ],
  );
  assert.equal(bridgeCloseCalls, 1);
});

test("waitForExit has a final bound after SIGKILL", async () => {
  const child = Object.assign(new EventEmitter(), {
    exitCode: null as number | null,
    signalCode: null as NodeJS.Signals | null,
    killSignals: [] as NodeJS.Signals[],
    kill(signal: NodeJS.Signals) {
      this.killSignals.push(signal);
      return true;
    },
  });

  await assert.rejects(
    waitForExitForTest(child, 5, 5, 5),
    /did not exit after SIGKILL/,
  );
  assert.deepEqual(child.killSignals, ["SIGTERM", "SIGKILL"]);
});

test("bridge close reports a nonzero bridge exit", async () => {
  const directory = await mkdtemp(path.join(process.cwd(), ".tmp-failing-bridge-"));
  const executable = path.join(directory, "bridge");
  await writeFile(executable, `#!/usr/bin/env node
import fs from "node:fs";
if (process.argv.includes("--cleanup")) process.exit(0);
process.stdin.once("data", () => {
  const result = ${JSON.stringify(prepared)};
  const metadataPath = process.env.INVPW_TYPESCRIPT_CLEANUP_PATH;
  const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  metadata.sessionToken = "c".repeat(32);
  fs.writeFileSync(metadataPath, JSON.stringify(metadata));
  result.profileDir = metadata.profileDir;
  result.cleanup = { ...metadata, metadataPath };
  if (result.cleanup.virtualDisplayPid === null) delete result.cleanup.virtualDisplayPid;
  process.stdout.write(JSON.stringify(result) + "\\n");
});
process.stdin.once("end", () => {
  process.stderr.write("reaper failed\\n");
  process.exit(7);
});
`);
  await chmod(executable, 0o755);

  try {
    const bridge = await startPythonBridge({ pythonExecutable: executable });
    await assert.rejects(bridge.close(), /code 7.*reaper failed/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("startup timeout invokes a separate cleanup helper for the owned profile", async () => {
  const directory = await mkdtemp(path.join(process.cwd(), ".tmp-startup-cleanup-"));
  const executable = path.join(directory, "bridge");
  const cleanupMarker = path.join(directory, "cleanup-ran");
  await writeFile(executable, `#!/usr/bin/env node
import fs from "node:fs";
const cleanupIndex = process.argv.indexOf("--cleanup");
if (cleanupIndex !== -1) {
  const metadata = JSON.parse(fs.readFileSync(process.argv[cleanupIndex + 1], "utf8"));
  if (metadata.removeProfile) fs.rmSync(metadata.profileDir, { recursive: true, force: true });
  fs.writeFileSync(${JSON.stringify(cleanupMarker)}, "confirmed");
  process.exit(0);
}
process.on("SIGTERM", () => process.exit(0));
process.stdin.once("data", () => {
  const metadataPath = process.env.INVPW_TYPESCRIPT_CLEANUP_PATH;
  const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  fs.mkdirSync(metadata.profileDir, { recursive: true });
});
setInterval(() => {}, 1000);
`);
  await chmod(executable, 0o755);

  try {
    await assert.rejects(
      startPythonBridgeForTest({ pythonExecutable: executable }, 20),
      /timed out after 20 ms/,
    );
    assert.equal(await readFile(cleanupMarker, "utf8"), "confirmed");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("bridge close escalates and waits for a child that ignores termination", async () => {
  const directory = await mkdtemp(path.join(process.cwd(), ".tmp-stubborn-bridge-"));
  const executable = path.join(directory, "bridge");
  const cleanupMarker = path.join(directory, "emergency-cleanup-ran");
  const childPrepared = { ...prepared, seed: 0 };
  await writeFile(executable, `#!/usr/bin/env node
import fs from "node:fs";
import nodePath from "node:path";
const cleanupIndex = process.argv.indexOf("--cleanup");
if (cleanupIndex !== -1) {
  const metadata = JSON.parse(fs.readFileSync(process.argv[cleanupIndex + 1], "utf8"));
  if (metadata.removeProfile) fs.rmSync(metadata.profileDir, { recursive: true, force: true });
  fs.writeFileSync(${JSON.stringify(cleanupMarker)}, "confirmed");
  process.exit(0);
}
process.on("SIGTERM", () => {});
process.stdin.once("data", () => {
  const prepared = ${JSON.stringify(childPrepared)};
  const metadataPath = process.env.INVPW_TYPESCRIPT_CLEANUP_PATH;
  if (metadataPath) {
    const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
    metadata.sessionToken = "c".repeat(32);
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    fs.mkdirSync(metadata.profileDir, { recursive: true });
    fs.writeFileSync(nodePath.join(metadata.profileDir, "owned"), "yes");
    prepared.profileDir = metadata.profileDir;
    prepared.cleanup = { ...metadata, metadataPath };
    if (prepared.cleanup.virtualDisplayPid === null) delete prepared.cleanup.virtualDisplayPid;
  }
  prepared.seed = process.pid;
  process.stdout.write(JSON.stringify(prepared) + "\\n");
});
setInterval(() => {}, 1000);
`);
  await chmod(executable, 0o755);
  let pid: number | undefined;
  let ownedProfile: string | undefined;

  try {
    const bridge = await startPythonBridge({ pythonExecutable: executable });
    pid = bridge.prepared.seed;
    ownedProfile = bridge.prepared.profileDir;
    await assert.rejects(bridge.close(), /terminated by SIGKILL/);

    assert.throws(() => process.kill(pid!, 0));
    assert.equal(await readFile(cleanupMarker, "utf8"), "confirmed");
    await assert.rejects(readFile(path.join(ownedProfile, "owned"), "utf8"));
  } finally {
    if (pid !== undefined) {
      try { process.kill(pid, "SIGKILL"); } catch {}
    }
    await rm(directory, { recursive: true, force: true });
  }
});
