# agentbreeder (npx launcher)

**Preview; clean-machine verification pending.** This package runs the AgentBreeder CLI from
PyPI through [uv](https://docs.astral.sh/uv/). It contains no Python code and has no dependencies
or install scripts.

```sh
npx agentbreeder@3.0.0-alpha.1 quickstart --dry-run
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
- Windows is not supported.

AgentBreeder 2.x (PyPI ≤ 2.7.3) is unmaintained and has known functional and security issues.
See <https://www.agentbreeder.io/docs/legacy-2x>.

Source, issues and docs: <https://github.com/agentbreeder/agentbreeder>.
