// Launcher tests (spec §5.3). node:test APIs available in Node 18 only.
"use strict";

const assert = require("node:assert");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const NPM_DIR = path.resolve(__dirname, "..");
const LAUNCHER = path.join(NPM_DIR, "bin", "agentbreeder.js");
const RELEASE = JSON.parse(fs.readFileSync(path.join(NPM_DIR, "release.json"), "utf8"));
const PACKAGE = JSON.parse(fs.readFileSync(path.join(NPM_DIR, "package.json"), "utf8"));
const { buildArgs, findUvx, launcherEnv, parseVersion, versionAtLeast } = require(LAUNCHER);

// A stub uvx: `--version` prints VERSION; otherwise it records argv (one per line) and selected
// env to $STUB_OUT, then exits with $STUB_EXIT. With $STUB_WAIT=trap it waits and records
// SIGTERM/SIGINT (exit 143/130); with $STUB_WAIT=die it waits with default signal handling.
function stub(dir, version = "0.11.6") {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "uvx");
  fs.writeFileSync(
    file,
    [
      "#!/bin/sh",
      `if [ "$1" = "--version" ]; then echo "uvx ${version} (stub)"; exit 0; fi`,
      'for a in "$@"; do printf "%s\\n" "$a"; done > "$STUB_OUT"',
      'printf "%s\\n" "${UV_INDEX_URL:-}" > "$STUB_OUT.env"',
      'printf "%s %s\\n" "${AGENTBREEDER_LAUNCHER:-}" "${AGENTBREEDER_LAUNCHER_VERSION:-}" > "$STUB_OUT.launcher"',
      'if [ "$STUB_WAIT" = trap ]; then',
      '  trap \'echo TERM > "$STUB_OUT.sig"; exit 143\' TERM',
      '  trap \'echo INT > "$STUB_OUT.sig"; exit 130\' INT',
      "fi",
      'if [ -n "$STUB_WAIT" ]; then',
      '  echo ready > "$STUB_OUT.ready"',
      "  while :; do sleep 0.05; done",
      "fi",
      'exit "${STUB_EXIT:-0}"',
      "",
    ].join("\n"),
    { mode: 0o755 }
  );
  return file;
}

function tmp() {
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "ab-launcher-")));
}

function run(args, { pathDirs, cwd, env = {} }) {
  return childProcess.spawnSync(process.execPath, [LAUNCHER, ...args], {
    cwd,
    encoding: "utf8",
    env: { HOME: os.homedir(), PATH: pathDirs.join(path.delimiter), ...env },
  });
}

// The stub's only external command is `sleep` (sh is its absolute shebang; printf is built in).
// A private directory keeps a system-wide uvx (/usr/bin/uvx on some distros) off PATH.
const TOOLS = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "ab-tools-")));
const SLEEP = ["/bin/sleep", "/usr/bin/sleep"].find((p) => fs.existsSync(p));
assert.ok(SLEEP, "sleep not found in /bin or /usr/bin");
fs.symlinkSync(SLEEP, path.join(TOOLS, "sleep"));
const BASE_PATH = [TOOLS];

test("runs uvx with the exact pinned argv and passes the environment through", () => {
  const root = tmp();
  const bin = path.join(root, "bin");
  stub(bin);
  const out = path.join(root, "argv");
  const result = run(["validate", "agent.yaml", "--flag=x y"], {
    pathDirs: [bin, ...BASE_PATH],
    cwd: root,
    env: { STUB_OUT: out, UV_INDEX_URL: "https://mirror.example/simple" },
  });
  assert.strictEqual(result.status, 0, result.stderr);
  const argv = fs.readFileSync(out, "utf8").split("\n").slice(0, -1);
  assert.deepStrictEqual(argv, [
    "--exclude-newer",
    RELEASE.exclude_newer,
    "--exclude-newer-package",
    `agentbreeder=${RELEASE.window_end}`,
    "-c",
    path.join(NPM_DIR, "constraints.txt"),
    "--from",
    `agentbreeder[eval]==${RELEASE.pypi_version}`,
    "agentbreeder",
    "validate",
    "agent.yaml",
    "--flag=x y",
  ]);
  assert.strictEqual(fs.readFileSync(out + ".env", "utf8"), "https://mirror.example/simple\n");
});

test("tells the CLI it runs under npx, with this package's version", () => {
  const root = tmp();
  const bin = path.join(root, "bin");
  stub(bin);
  const out = path.join(root, "argv");
  const result = run(["status"], {
    pathDirs: [bin, ...BASE_PATH],
    cwd: root,
    // A caller's own values are replaced, never passed on.
    env: { STUB_OUT: out, AGENTBREEDER_LAUNCHER: "x", AGENTBREEDER_LAUNCHER_VERSION: "$(id)" },
  });
  assert.strictEqual(result.status, 0, result.stderr);
  assert.strictEqual(fs.readFileSync(out + ".launcher", "utf8"), `npx ${PACKAGE.version}\n`);
  assert.match(PACKAGE.version, /^\d+\.\d+\.\d+(-(alpha|beta|rc)\.\d+)?$/);
  const env = launcherEnv({ KEEP: "1" }, { version: "3.0.0-alpha.2" });
  assert.deepStrictEqual(env, {
    KEEP: "1",
    AGENTBREEDER_LAUNCHER: "npx",
    AGENTBREEDER_LAUNCHER_VERSION: "3.0.0-alpha.2",
  });
});

test("release.json pins one exact PyPI version and a 30-day agentbreeder window", () => {
  assert.match(RELEASE.pypi_version, /^\d+\.\d+\.\d+((a|b|rc)\d+)?$/);
  const day = 24 * 60 * 60 * 1000;
  const window = Date.parse(RELEASE.window_end) - Date.parse(RELEASE.exclude_newer);
  assert.strictEqual(window, 30 * day);
  assert.strictEqual(RELEASE.min_uv, "0.11.6");
  assert.deepStrictEqual(buildArgs(RELEASE, "/c.txt", []).slice(-2), [
    `agentbreeder[eval]==${RELEASE.pypi_version}`,
    "agentbreeder",
  ]);
});

test("forwards the exit code", () => {
  const root = tmp();
  const bin = path.join(root, "bin");
  stub(bin);
  const result = run(["status"], {
    pathDirs: [bin, ...BASE_PATH],
    cwd: root,
    env: { STUB_OUT: path.join(root, "argv"), STUB_EXIT: "3" },
  });
  assert.strictEqual(result.status, 3);
});

test("skips uvx under node_modules", () => {
  const root = tmp();
  const evil = path.join(root, "node_modules", ".bin");
  stub(evil);
  const good = path.join(root, "tools");
  stub(good);
  const found = findUvx([evil, good].join(path.delimiter), os.tmpdir());
  assert.deepStrictEqual(found, { path: path.join(good, "uvx") });
  assert.deepStrictEqual(findUvx(evil, os.tmpdir()), {});
});

test("refuses uvx from the current directory or a relative PATH entry", () => {
  const root = tmp();
  stub(root);
  stub(path.join(root, "bin"));
  for (const entry of ["", ".", "bin", root]) {
    const found = findUvx(entry, root);
    assert.ok(found.refused, `PATH entry ${JSON.stringify(entry)} was not refused`);
  }
  const out = path.join(root, "argv");
  const result = run(["status"], { pathDirs: [".", ...BASE_PATH], cwd: root, env: { STUB_OUT: out } });
  assert.strictEqual(result.status, 126);
  assert.match(result.stderr, /refusing to run .*current directory/);
  assert.ok(!fs.existsSync(out), "the refused uvx ran");
});

test("allows an absolute uvx below the current directory", () => {
  // e.g. running from $HOME with ~/.local/bin on PATH
  const root = tmp();
  const bin = path.join(root, ".local", "bin");
  stub(bin);
  assert.deepStrictEqual(findUvx(bin, root), { path: path.join(bin, "uvx") });
});

test("exits 127 with the uv install command when uvx is missing", () => {
  const root = tmp();
  const result = run(["status"], { pathDirs: BASE_PATH, cwd: root });
  assert.strictEqual(result.status, 127);
  assert.match(result.stderr, /uvx.*not found/);
  assert.match(result.stderr, /curl -LsSf https:\/\/astral\.sh\/uv\/install\.sh \| sh/);
});

test("refuses an old uv with an upgrade message", () => {
  const root = tmp();
  const bin = path.join(root, "bin");
  stub(bin, "0.11.5");
  const out = path.join(root, "argv");
  const result = run(["status"], { pathDirs: [bin, ...BASE_PATH], cwd: root, env: { STUB_OUT: out } });
  assert.strictEqual(result.status, 1);
  assert.match(result.stderr, /uv 0\.11\.6 or newer is required \(found 0\.11\.5/);
  assert.match(result.stderr, /uv self update/);
  assert.ok(!fs.existsSync(out));
});

test("version parsing and comparison", () => {
  assert.deepStrictEqual(parseVersion("uvx 0.11.6 (65950801c 2026-04-09)"), [0, 11, 6]);
  assert.strictEqual(parseVersion("garbage"), null);
  assert.ok(versionAtLeast([0, 11, 6], [0, 11, 6]));
  assert.ok(versionAtLeast([0, 12, 0], [0, 11, 6]));
  assert.ok(versionAtLeast([1, 0, 0], [0, 11, 6]));
  assert.ok(!versionAtLeast([0, 11, 5], [0, 11, 6]));
  assert.ok(!versionAtLeast([0, 9, 99], [0, 11, 6]));
});

test("--launcher-version prints versions, the uvx path and the index warning", () => {
  const root = tmp();
  const bin = path.join(root, "bin");
  stub(bin);
  const out = path.join(root, "argv");
  const plain = run(["--launcher-version"], {
    pathDirs: [bin, ...BASE_PATH],
    cwd: root,
    env: { STUB_OUT: out },
  });
  assert.strictEqual(plain.status, 0, plain.stderr);
  assert.ok(plain.stdout.includes(`agentbreeder ${PACKAGE.version}`));
  assert.ok(plain.stdout.includes(`agentbreeder[eval]==${RELEASE.pypi_version}`));
  assert.ok(plain.stdout.includes(`uvx: ${path.join(bin, "uvx")} (0.11.6)`));
  assert.doesNotMatch(plain.stdout, /warning/);
  assert.ok(!fs.existsSync(out), "--launcher-version must not run agentbreeder");

  const warned = run(["--launcher-version"], {
    pathDirs: [bin, ...BASE_PATH],
    cwd: root,
    env: { UV_EXTRA_INDEX_URL: "https://test.pypi.org/simple/" },
  });
  assert.match(warned.stdout, /warning: UV_EXTRA_INDEX_URL set/);
  const overridden = run(["--launcher-version"], {
    pathDirs: [bin, ...BASE_PATH],
    cwd: root,
    env: { UV_OVERRIDE: "/tmp/o.txt", UV_PRERELEASE: "allow" },
  });
  assert.match(overridden.stdout, /warning: UV_PRERELEASE, UV_OVERRIDE set/);

  const missing = run(["--launcher-version"], { pathDirs: BASE_PATH, cwd: root });
  assert.strictEqual(missing.status, 0);
  assert.match(missing.stdout, /uvx: not found on PATH/);
});

async function startWaiting(mode) {
  const root = tmp();
  const bin = path.join(root, "bin");
  stub(bin);
  const out = path.join(root, "argv");
  const child = childProcess.spawn(process.execPath, [LAUNCHER, "up"], {
    cwd: root,
    env: { PATH: [bin, ...BASE_PATH].join(path.delimiter), STUB_OUT: out, STUB_WAIT: mode },
    stdio: "ignore", // not a terminal: the launcher forwards SIGINT too
  });
  const exited = new Promise((resolve) => child.on("exit", (code, signal) => resolve({ code, signal })));
  const deadline = Date.now() + 10000;
  while (!fs.existsSync(out + ".ready")) {
    if (Date.now() > deadline) {
      child.kill("SIGKILL");
      throw new Error("stub never started");
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return { child, exited, out };
}

for (const [signal, name, status] of [
  ["SIGTERM", "TERM", 143],
  ["SIGINT", "INT", 130],
]) {
  test(`forwards ${signal} to uvx and exits with its status`, { timeout: 20000 }, async () => {
    const { child, exited, out } = await startWaiting("trap");
    child.kill(signal);
    const { code } = await exited;
    assert.strictEqual(fs.readFileSync(out + ".sig", "utf8"), `${name}\n`);
    assert.strictEqual(code, status);
  });
}

test("re-raises the signal that killed uvx", { timeout: 20000 }, async () => {
  const { child, exited } = await startWaiting("die");
  child.kill("SIGTERM");
  const { code, signal } = await exited;
  assert.strictEqual(signal, "SIGTERM", `exited with code ${code}`);
});

test("skips a uvx that links into node_modules", () => {
  const root = tmp();
  const pkgBin = path.join(root, "lib", "node_modules", "uvx", "bin");
  stub(pkgBin);
  const globalBin = path.join(root, "globalbin");
  fs.mkdirSync(globalBin);
  fs.symlinkSync(path.join(pkgBin, "uvx"), path.join(globalBin, "uvx"));
  const good = path.join(root, "tools");
  stub(good);
  assert.deepStrictEqual(findUvx([globalBin, good].join(path.delimiter), os.tmpdir()), {
    path: path.join(good, "uvx"),
  });
});

test("package.json: unscoped, no scripts or dependencies, files allowlist", () => {
  assert.strictEqual(PACKAGE.name, "agentbreeder");
  assert.deepStrictEqual(PACKAGE.bin, { agentbreeder: "bin/agentbreeder.js" });
  assert.deepStrictEqual(PACKAGE.engines, { node: ">=18" });
  for (const key of ["scripts", "dependencies", "optionalDependencies", "peerDependencies"]) {
    assert.ok(!(key in PACKAGE), `package.json must not have ${key}`);
  }
  const mode = fs.statSync(LAUNCHER).mode;
  assert.ok(mode & 0o100, "bin/agentbreeder.js must be executable");
});

test("npm pack ships only the allowlisted files", () => {
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const result = childProcess.spawnSync(npm, ["pack", "--dry-run", "--json", "--ignore-scripts"], {
    cwd: NPM_DIR,
    encoding: "utf8",
  });
  assert.strictEqual(result.status, 0, result.stderr);
  const files = JSON.parse(result.stdout)[0].files.map((f) => f.path).sort();
  assert.deepStrictEqual(files, [
    "LICENSE",
    "README.md",
    "bin/agentbreeder.js",
    "constraints.txt",
    "package.json",
    "release.json",
  ]);
});
