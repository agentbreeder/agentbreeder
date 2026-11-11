# agentbreeder (npx launcher)

**Preview.** This package runs the AgentBreeder CLI from PyPI through
[uv](https://docs.astral.sh/uv/). It contains no Python code and has no dependencies or install
scripts.

Verified with `3.0.0-alpha.2` from the public registries on 2026-11-11, on macOS with Docker
Desktop: a real `quickstart`, and `agentbreeder up` with the Northwind example (61/61 on local
Docker and on a local `kind` cluster, driven through its API). Nothing has run on Linux with this
version; `3.0.0-alpha.1` ran `--version` and `doctor` on Ubuntu 24.04 (Node 18.19.1).

```sh
npx agentbreeder@3.0.0-alpha.2 quickstart --dry-run
```

Each launcher version runs one exact PyPI version:

```sh
uvx --exclude-newer <release time> --exclude-newer-package agentbreeder=<release time + 30 days> \
    -c constraints.txt --from "agentbreeder[eval]==<version>" agentbreeder <args...>
```

- `--exclude-newer` and `constraints.txt` (the dependency versions the release was tested with)
  bound what a cold run resolves. They do not pin hashes.
- Requirements: Node.js 18 or newer and uv 0.11.6 or newer (`uvx` on `PATH`). If `uvx` is
  missing, the launcher exits with status 127 and prints the uv install command.
- `uvx` is looked up on `PATH`, skipping `node_modules` directories; a `uvx` found through the
  current directory (an empty or relative `PATH` entry, or the current directory itself) is
  refused.
- The environment is passed through unchanged, so your uv settings apply: index settings
  (`UV_INDEX`, `UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_EXTRA_INDEX_URL`, `UV_FIND_LINKS`,
  `UV_INDEX_STRATEGY`, a user `uv.toml` or `UV_CONFIG_FILE`) decide where the exact pin is
  satisfied from, and `UV_PRERELEASE`, `UV_OVERRIDE` and `UV_CONSTRAINT` change what resolves.
- `npx agentbreeder --launcher-version` prints the npm and PyPI versions, the `uvx` it would run,
  and a warning when one of those variables is set. It does not start Python.
- `npx` does not leave an `agentbreeder` command on your `PATH`. Repeat the prefix for each
  command (`npx agentbreeder@3.0.0-alpha.2 up`), or install the command with
  `uv tool install "agentbreeder[eval]==3.0.0a2"`. A bare `agentbreeder` that fails with
  `No module named 'cli'` or is not `3.0.0a2` comes from an older install earlier on your `PATH`;
  see <https://www.agentbreeder.io/docs/install#an-older-agentbreeder-on-your-path>.
- Windows is not supported.

AgentBreeder 2.x (PyPI ≤ 2.7.3) is unmaintained and has known functional and security issues.
See <https://www.agentbreeder.io/docs/legacy-2x>.

Source, issues and docs: <https://github.com/agentbreeder/agentbreeder>.
