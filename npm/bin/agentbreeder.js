#!/usr/bin/env node
// npx launcher for AgentBreeder (spec §5.3, #782).
//
// Runs the pinned PyPI release through uv:
//   uvx --exclude-newer <exclude_newer> --exclude-newer-package agentbreeder=<window_end>
//       -c <constraints.txt> --from agentbreeder[eval]==<pypi_version> agentbreeder <args...>
//
// No dependencies, no install scripts, no shell. The environment is passed through unchanged.
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const PACKAGE_DIR = path.resolve(__dirname, "..");
const INSTALL_HELP = [
  "agentbreeder: `uvx` (part of uv) was not found on PATH.",
  "Install uv, then run this command again:",
  "  macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh",
  "  or see https://docs.astral.sh/uv/getting-started/installation/",
].join("\n");
// uv settings that change where packages come from or which versions resolve. Any of them means
// the pinned run may not be what the release tested.
const RESOLUTION_VARIABLES = [
  "UV_INDEX",
  "UV_INDEX_URL",
  "UV_DEFAULT_INDEX",
  "UV_EXTRA_INDEX_URL",
  "UV_FIND_LINKS",
  "UV_INDEX_STRATEGY",
  "UV_PRERELEASE",
  "UV_OVERRIDE",
  "UV_CONSTRAINT",
  "UV_CONFIG_FILE",
];

function readJson(name) {
  return JSON.parse(fs.readFileSync(path.join(PACKAGE_DIR, name), "utf8"));
}

function realpathOr(p) {
  try {
    return fs.realpathSync(p);
  } catch {
    return path.resolve(p);
  }
}

function isExecutableFile(p) {
  try {
    if (!fs.statSync(p).isFile()) return false;
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

// Returns {path} for the first usable uvx on PATH, {refused} when that match comes from the
// current directory, or {} when there is none. PATH entries with a node_modules segment are
// skipped, so a package's own node_modules/.bin cannot supply uvx. An empty or relative PATH
// entry, or one that is the current directory itself, resolves via cwd: a match there is refused
// (a checked-out repository must not be able to supply uvx).
function inNodeModules(p) {
  return p.split(path.sep).includes("node_modules");
}

function findUvx(envPath, cwd) {
  const name = process.platform === "win32" ? "uvx.exe" : "uvx";
  const realCwd = realpathOr(cwd);
  for (const entry of (envPath || "").split(path.delimiter)) {
    const dir = path.resolve(cwd, entry);
    if (inNodeModules(dir)) continue;
    const candidate = path.join(dir, name);
    if (!isExecutableFile(candidate)) continue;
    // e.g. a global npm package named uvx: a link from a bin directory into node_modules.
    if (inNodeModules(realpathOr(candidate))) continue;
    if (!path.isAbsolute(entry) || realpathOr(dir) === realCwd) {
      return { refused: candidate };
    }
    return { path: candidate };
  }
  return {};
}

function parseVersion(text) {
  const match = /(\d+)\.(\d+)\.(\d+)/.exec(text || "");
  return match ? match.slice(1, 4).map(Number) : null;
}

function versionAtLeast(have, want) {
  for (let i = 0; i < 3; i += 1) {
    if (have[i] !== want[i]) return have[i] > want[i];
  }
  return true;
}

function buildArgs(release, constraints, userArgs) {
  return [
    "--exclude-newer",
    release.exclude_newer,
    "--exclude-newer-package",
    `agentbreeder=${release.window_end}`,
    "-c",
    constraints,
    "--from",
    `agentbreeder[eval]==${release.pypi_version}`,
    "agentbreeder",
    ...userArgs,
  ];
}

function uvVersion(uvx) {
  const result = childProcess.spawnSync(uvx, ["--version"], {
    encoding: "utf8",
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error || result.status !== 0) return null;
  return parseVersion(result.stdout);
}

function resolutionWarnings(env) {
  return RESOLUTION_VARIABLES.filter((name) => env[name]);
}

function launcherVersion(pkg, release, found, version) {
  const lines = [
    `npm package: agentbreeder ${pkg.version}`,
    `PyPI version: agentbreeder[eval]==${release.pypi_version}`,
    `exclude-newer: ${release.exclude_newer} (agentbreeder: ${release.window_end})`,
  ];
  if (found.path) {
    lines.push(`uvx: ${found.path} (${version ? version.join(".") : "version unknown"})`);
  } else if (found.refused) {
    lines.push(`uvx: refused ${found.refused} (from the current directory)`);
  } else {
    lines.push("uvx: not found on PATH");
  }
  const set = resolutionWarnings(process.env);
  if (set.length) {
    lines.push(
      `warning: ${set.join(", ")} set; these uv settings change where agentbreeder==` +
        `${release.pypi_version} and its dependencies come from or which versions resolve.`
    );
  }
  lines.push("note: uv also reads index settings from uv.toml files.");
  process.stdout.write(lines.join("\n") + "\n");
}

function main(argv) {
  const pkg = readJson("package.json");
  const release = readJson("release.json");
  const found = findUvx(process.env.PATH, process.cwd());

  if (argv[0] === "--launcher-version") {
    launcherVersion(pkg, release, found, found.path ? uvVersion(found.path) : null);
    return;
  }
  if (found.refused) {
    process.stderr.write(
      `agentbreeder: refusing to run ${found.refused}: it comes from the current directory.\n` +
        "Remove that directory from PATH or run from elsewhere.\n"
    );
    process.exit(126);
  }
  if (!found.path) {
    process.stderr.write(INSTALL_HELP + "\n");
    process.exit(127);
  }
  const minimum = parseVersion(release.min_uv);
  const have = uvVersion(found.path);
  if (!have || !versionAtLeast(have, minimum)) {
    process.stderr.write(
      `agentbreeder: uv ${release.min_uv} or newer is required ` +
        `(found ${have ? have.join(".") : "an unknown version"} at ${found.path}).\n` +
        "Upgrade with `uv self update`, or reinstall from " +
        "https://docs.astral.sh/uv/getting-started/installation/\n"
    );
    process.exit(1);
  }

  const constraints = path.join(PACKAGE_DIR, "constraints.txt");
  const child = childProcess.spawn(found.path, buildArgs(release, constraints, argv), {
    stdio: "inherit",
    shell: false,
  });
  const forward = (signal) => () => {
    if (child.exitCode === null && child.signalCode === null) child.kill(signal);
  };
  // On a terminal, Ctrl-C already reaches uvx through the foreground process group; sending a
  // second SIGINT would interrupt Python's cleanup. Stay alive and let the terminal deliver it.
  // tty.isatty is used rather than process.stdin, which would start reading the terminal; fd 2
  // covers redirected stdin. Limitation: on a terminal, a SIGINT sent only to this process
  // (`kill -INT`) is not forwarded; SIGTERM always is.
  const tty = require("node:tty");
  const interactive = tty.isatty(0) || tty.isatty(2);
  const handlers = {
    SIGINT: interactive ? () => {} : forward("SIGINT"),
    SIGTERM: forward("SIGTERM"),
  };
  for (const [signal, handler] of Object.entries(handlers)) process.on(signal, handler);

  child.on("error", (error) => {
    process.stderr.write(`agentbreeder: could not start ${found.path}: ${error.message}\n`);
    process.exit(127);
  });
  child.on("exit", (code, signal) => {
    if (signal) {
      // Re-raise so our parent sees the same signal; fall back to 128 + n if it is ignored.
      for (const [name, handler] of Object.entries(handlers)) process.removeListener(name, handler);
      process.kill(process.pid, signal);
      process.exit(128 + (require("node:os").constants.signals[signal] || 0));
    }
    process.exit(code);
  });
}

if (require.main === module) {
  main(process.argv.slice(2));
}

module.exports = { buildArgs, findUvx, parseVersion, versionAtLeast };
