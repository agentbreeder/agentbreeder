# Remote registry sources (M9b, #829): design

- Status: **r3, reviews converged** (architecture, UX and `/security` by Claude Fable, three rounds;
  §16). Awaiting owner sign-off on D1–D7 (§14).
- Epic #812, milestone M9b (#829), lane C. Plan of record: `docs/superpowers/plans/2026-11-11-console-epic.md`
  r3 (wins where this spec differs). Code starts after M10 (#826) merges (lane order).
- Builds on: `docs/superpowers/specs/2026-11-07-agent-yaml-build-design.md` §6 (refs, registries,
  `agent.lock`), `docs/registry.md`, `src/agentbreeder/registry.py`, `src/agentbreeder/lock.py`.
- Later consumers: M13 (#763, agent package, depends on M9b), M19 (A2A `agent` kind).

## 1. Problem

Registry versions today come only from local directories: the project's `./registry`, each
`AGENTBREEDER_REGISTRY_PATH` entry, and the built-in registry in the wheel. A team that wants one
reviewed set of tools and prompts for many agents has to copy version directories between repos
(or `registry import` them, M9a). Nothing records where a copied version came from, and a
consumer cannot say "the `tool/geo@1.2.0` from our org registry at commit X".

## 2. Goal

An `agent.yaml` names remote registry sources. `build` fetches the refs it needs from them,
hashes the copy exactly as today, and records the source and its pin in `agent.lock`, so a
locked build is reproducible without trusting the remote to stay the same.

Acceptance (from #829, run on its own):

1. A tool from a separate git repo builds into an agent.
2. Changing the remote (new commits, a force-push, a re-tagged image) does not change a locked build.
3. An unreachable source fails with a clear message naming the source, the host and the remedy.

## 3. Scope and non-goals

In scope (M9b):

- `registries:` in `agent.yaml`; three source types: **git** at a commit, **OCI** at a digest,
  **HTTP index** at an index hash. Git ships first (it is the acceptance path); OCI and HTTP each
  ship only after their own end-to-end run (§11). Docs never claim a type that has not run.
- A per-user **trusted-source list** and `registry trust`; `registry pin` to turn a branch or tag
  into a pin.
- A cache of extracted versions, `registry fetch`, `--offline`, `registry cache list|prune`.
- `agent.lock` format v2 (§7), written when `agent.yaml` declares `registries:`.
- Remote **`mcp`** versions (owner, D5): `stack.yaml` also gains `registries:` for them, and
  `stack.lock` follows the same v1/v2 rule (§6.6).
- Layouts for an **organization registry repo** and the **agent repos** that consume it (§9).
- The **standard library** stays the built-in registry in the wheel; additions there each run end
  to end before they ship (§10).
- The console shows each version's source and fetch state (§8.5).

Non-goals (M9b):

- Remote `runtime` and `service` versions (refused, §6.4): services and runtimes run with
  host-level reach and stay local or built-in. The MCP relay (`service/mcp-relay`) stays
  built-in only (`RELAY_RESERVED`, unchanged). Allowed kinds from remotes (owner, D5): `prompt`,
  `eval`, `knowledge`, `tool`, `mcp`, and `agent` once M13 defines that kind (§6.4).
- Floating pins (branches, tags, `latest`, an unpinned index) in `agent.yaml`. Every source is
  pinned where it is declared (§5), so a reviewer sees a pin change in the diff. `registry pin`
  removes the copy-paste work.
- Signatures or provenance (sigstore, GPG, SLSA). A pin proves "same bytes as when locked", not
  who wrote them (§8.6).
- Authenticated OCI and HTTP pulls. Private content goes through git, which uses the user's own
  git credentials. Anonymous OCI/HTTP only in M9b.
- SHA-256 git object format (SHA-1 commits only; refused with `SOURCE_INVALID`).
- A hosted standard-library index, a registry server, publishing to OCI from AgentBreeder
  (publishers use `oras`, §6.2), `registry outdated`.
- Windows.

## 4. Roots and resolution

### 4.1 One root type

Today `search` is `list[Path]` and the lock label is derived from the path (`lock.py:61-68`).
M9b introduces one type, in `registry.py`:

```python
@dataclass(frozen=True)
class Root:
    path: Path                 # directory holding kind/name/version (a staging dir for a remote)
    label: str                 # "registry", "builtin", "/abs/path", or "remote:acme"
    source: Source | None      # the remote's declared pin (None for local roots)
    kinds: frozenset[str]      # kinds this root may provide (remotes: prompt, eval, knowledge, tool)
```

A remote becomes a **per-build staging root**: a private `mkdtemp` (0700) holding only the
version directories the build asked for, materialised from the pin-authenticated object (§6) or
from the verified cache (§8.1). `registry.resolve`, `copy_and_hash` and `read_manifest` then work
on it unchanged, so the entry checks (regular files and directories only, no symlinks or
hardlinks, UTF-8 names, `MAX_FILES`, `MAX_BYTES`) and the tree hash are the same code for local
and remote versions. `materialise` writes the lock entry from the `Root`, not from a path.
Stack callers (`services/engine.py:123`, `services/mcp_stack.py`) keep passing local roots only.

### 4.2 Plain build

Search order:

1. the project's `./registry`;
2. each `AGENTBREEDER_REGISTRY_PATH` entry;
3. each `registries:` source, in the order declared;
4. the built-in registry.

- A ref found in several roots with **different content** is `REF_CONFLICT` (message in §12),
  naming both roots and hashes; a remote is named by name, type, host and short pin, never by a
  cache path. Same content in several roots is fine; the first wins.
- So a remote never silently shadows a local version, and a local copy never silently shadows a
  remote one. The conflict check needs each source's listing (ref → tree hash at the pin). A pin
  is immutable, so its listing is cached (`listings/`, §8.1): **a plain build needs the network
  once per source per pin**, to fetch the listing, and is offline after that. An unreachable
  source with no cached listing fails the build even if no ref ends up coming from it; the
  message says the fetch is "needed once per pin to check for conflicts" and to remove the
  source from `agent.yaml` if it is no longer used. (UX r1 #6 asked to skip unreachable sources; rejected: which source a
  ref would come from is only known after every listing is read, and a skipped source could hide
  a conflict.)
- A remote version of a refused kind (§6.4) is ignored for resolution.

### 4.3 `--locked`

Changed from today: `lock.py:110-119` currently resolves across **all** roots (so `REF_CONFLICT`
and `REF_NOT_FOUND` fire) and then redirects to the locked source. M9b makes `--locked` resolve
each ref **only from its locked root**, with no cross-root check, for local and remote entries
alike. Reasons: a cross-root check would force a fetch of every source, defeating `--offline` and
cache-only CI; and the locked hash already fixes the bytes, so a shadowing copy elsewhere cannot
change the build. Unused declared sources are not fetched.

The locked root must still be one that is **configured now**: a local label must equal the label
of a current `roots()` entry (`registry`, an `AGENTBREEDER_REGISTRY_PATH` entry, `builtin`), and a
`remote:` label must name a declared source. Anything else (an absolute or `..` path the lock
names but the user never configured) is `LOCK_STALE`. A planted lock therefore cannot make a build
read an arbitrary directory.

- `registry publish --to` refuses a cache or staging path (`REGISTRY_READ_ONLY`).
- `--update` on a remote ref: `--update applies to path refs only; change the pin in agent.yaml
  to move a source`.

## 5. `agent.yaml`: `registries:`

```yaml
registries:
  - name: acme                      # a label for messages and agent.lock; [a-z0-9][a-z0-9-]{0,62}
    git: https://github.com/acme/agent-registry.git
    commit: 3f2a9c1d0b7e4a5f6c8d9e0f1a2b3c4d5e6f7a8b   # main on 2026-11-11 (from `registry pin`)
    path: registry                  # optional: subdirectory holding kind/name/version; default "."
  - name: acme-images
    oci: ghcr.io/acme/agent-registry@sha256:9b1e…    # digest required; tags refused
  - name: community
    http: https://registry.example.org/agentbreeder/index.json
    index_sha256: sha256:51d0…      # required: the index is pinned like any other source
```

- Optional, additive. Without it, everything behaves exactly as today (lock v1 included). Strict
  YAML as today: unknown keys are errors. `name` is a label only; refs carry no source
  (`tool/geo@1.2.0`), and `build` prints where each remote ref came from (§12).
- Exactly one of `git`, `oci`, `http` per entry.
- **URLs.** Git: `https://host[:port]/path` or `ssh://[user@]host[:port]/path`; scp-style
  `user@host:path` is accepted and normalised to `ssh://user@host/path`. `file://`, local paths,
  `ext::`, `http://` and every other transport are refused. **No credentials in URLs:** userinfo
  in an `https` URL, and a query or fragment in any URL, are `SOURCE_INVALID` (they would be
  copied into the committed lock, `build.json` and the console). A bare ssh `user@` is allowed.
  A host or path segment that starts with `-` is refused (`-oProxyCommand=…`).
- **Canonical form.** Every URL is reduced to one canonical string, and trust matching (§8.3),
  the lock's `sources`, messages, and the `git`/`urllib` argv all use that string, never the raw
  text: scheme lower-cased; host lower-cased ASCII (non-ASCII/IDN hosts refused), no trailing
  dot; default port removed (`:443`, `:22`); ssh user kept and matched exactly; path segments
  non-empty, no `.` or `..`, no `%` (no percent-encoding), no `~`, no backslash; scp-style
  `user@host:path` becomes `ssh://user@host/path` (a `~` path is refused). Anything that does
  not canonicalise is `SOURCE_INVALID`.
- `commit` must be a full 40-hex SHA-1. `main`, `v1.2.0` or a short SHA give `SOURCE_UNPINNED`
  (message in §12) pointing at `registry pin`.
- OCI references need `@sha256:<64 hex>`; a tag gives `SOURCE_UNPINNED` pointing at `registry pin`.
- HTTP needs `index_sha256` (the hash of the `index.json` bytes).
- `path` must be relative, no `..`, no leading `/` or `:` (git pathspec magic).
- The JSON Schema (`docs/manifest.schema.json`) and `docs/agent-yaml.md` gain the block (an
  example in the docs; Northwind stays without one).

`agentbreeder registry pin NAME --to REF [--write] [AGENT_YAML]` resolves a branch or tag
(`git ls-remote`, OCI manifest HEAD, HTTP index fetch and hash) and prints the YAML block, e.g.
`commit: 3f2a9c1d…7a8b   # main on 2026-11-11`; `--write` replaces only that source's pin line in
`agent.yaml` (round-trip check: the rewritten file must parse to the same document except the pin).
`registry pin --git URL --to REF --name NAME` prints a new block for a source not yet declared.
For an HTTP source `--to` is omitted: `registry pin community` re-hashes the current index.
`REF` must match `[A-Za-z0-9._/-]+`, must not start with `-`, and is passed after `--`. `pin`
needs the source to be trusted (§8.3); it never builds.

## 6. Source types

All three end the same way: the requested version directories, materialised into the build's
private staging root (§4.1), then `resolve` → `copy_and_hash` into the build context →
`read_manifest`. Nothing from a remote reaches a build context, or runs on the host, by any other
path. Archive-level limits (below) are separate from the per-version `MAX_FILES`/`MAX_BYTES`,
which still apply to each version in `copy_and_hash`. Only the requested `kind/name/version/`
subtrees are materialised; listings come from names only.

**Network and process limits (all types):** every fetch checks the trusted-source list first
(§8.3), in every mode; socket timeout 30 s, total 120 s per source (`SOURCE_TIMEOUT`); download
cap 200 MB per source (`SOURCE_TOO_LARGE`); `index.json` and OCI manifests capped at 4 MB, token
responses at 64 KB.

### 6.1 Git at a commit

- Runs the `git` CLI (no new Python dependency). `doctor` warns when a project declares a git
  source and `git` is missing; `build` fails with `SOURCE_TOOL_MISSING`.
- **Fresh bare repo per fetch.** Each fetch runs in a new private `mkdtemp` bare repository that
  is deleted afterwards. No git repository persists in the cache, so no cached git `config` can
  ever be executed (`core.sshCommand`, `credential.helper`, `core.fsmonitor`, `url.*.insteadOf`).
  Only verified extracted trees are cached (§8.1).
- **Fetch:** `git fetch --depth 1 --no-tags -- <url> <commit>`. If the server refuses fetch by
  SHA, fall back once to a full, unfiltered `git fetch --no-tags -- <url>
  "+refs/heads/*:refs/ab/heads/*" "+refs/tags/*:refs/ab/tags/*"` (without the refspecs a
  remote-less bare repo fetches only `HEAD`; a partial clone is not used, because a remote-less
  repo has no promisor to fetch blobs from later), then `git cat-file -e <commit>^{commit}`;
  `SOURCE_COMMIT_MISSING` if absent (message in §12).
- **Download cap for git:** the fetch runs in its own process group; the temp repo's size is
  polled every 250 ms and the group is killed when it passes 200 MB (`SOURCE_TOO_LARGE`), as is
  the 120 s total timeout. After the fetch all objects are local, so the size checks below read
  no network.
- **Read without a checkout:** `git ls-tree -r -z --full-tree <commit> -- <path>/<kind>/<name>/<version>`
  per requested version. Modes `100644` and `100755` are files (exec bit kept). `120000`
  (symlink) and `160000` (submodule) are `ENTRY_UNSAFE` naming the path. Sizes are summed with
  `git cat-file --batch-check` before any blob is read (`MAX_BYTES` per version, the download
  cap per source). Blobs are read with `git cat-file --batch` and written `O_EXCL | O_NOFOLLOW`
  into the staging root. Git LFS pointer blobs (`version https://git-lfs.github.com/spec/v1`
  first line) are `ENTRY_UNSAFE` ("LFS files are not supported in registry versions").
  `__pycache__` and `*.pyc`/`*.pyo` are skipped, as `registry.py:195-196` does. The listing for
  the conflict check is `git ls-tree -z --name-only` at the `kind/name` and `version` levels
  (bounded output).
- **Process hardening, every invocation:**
  - argv only (no shell); cwd an empty private temp dir; always `--git-dir=<tmp>`;
    `GIT_CEILING_DIRECTORIES` set, so no repository is discovered from the project tree;
  - environment rebuilt from an allow-list (`PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`,
    `SSH_AUTH_SOCK`, `SSL_CERT_FILE`, `GIT_SSL_CAINFO`, and the proxy variables `HTTPS_PROXY`,
    `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY` in both cases, which `urllib` also honours for OCI and
    HTTP sources, so all three types behave alike behind a corporate proxy; trust and private-address
    checks apply to the target host, not the proxy); every other `GIT_*` variable dropped (notably
    `GIT_ALLOW_PROTOCOL`, `GIT_SSH_COMMAND`, `GIT_CONFIG_*`, `GIT_PROXY_COMMAND`, `GIT_ASKPASS`),
    then set: `GIT_TERMINAL_PROMPT=0`, `GIT_LITERAL_PATHSPECS=1`,
    `GIT_SSH_COMMAND=ssh -o BatchMode=yes` (unknown host keys fail closed; `~/.ssh/config` and
    the agent still apply);
  - `-c protocol.allow=never -c protocol.https.allow=always -c protocol.ssh.allow=always
    -c http.followRedirects=false -c credential.interactive=false -c core.hooksPath=/dev/null
    -c fetch.fsckObjects=true -c transfer.fsckObjects=true -c submodule.recurse=false
    -c gc.auto=0`;
  - `--` before every pathspec; the URL refused if it (or its host/path) begins with `-`;
  - stderr captured, control characters stripped, any URL userinfo redacted before display.
  - The user's `~/.gitconfig` credential helpers still apply (that is how private org repos work);
    `GIT_CONFIG_NOSYSTEM` is not set, but the `-c` options above override system and global config.
- Auth failures (`Authentication failed`, `Permission denied (publickey)`, HTTP 401/403) map to
  `SOURCE_AUTH_FAILED`; DNS, TCP and TLS failures to `SOURCE_UNREACHABLE` (§12).

### 6.2 OCI at a digest

- Artifact layout (documented for publishers): an OCI image manifest
  (`application/vnd.oci.image.manifest.v1+json`) with
  `artifactType: application/vnd.agentbreeder.registry.v1` and **exactly one** layer of media type
  `application/vnd.agentbreeder.registry.layer.v1.tar+gzip` holding `kind/name/version/...` at the
  archive root. Image indexes and any other shape are `SOURCE_INVALID`. Publishers build it with
  `agentbreeder registry pack DIR -o registry.tar.gz` (deterministic: sorted entries, zero
  mtimes/uid/gid, modes 0644/0755 only) and push with `oras push`. AgentBreeder does not push.
- Pull: a small OCI distribution client on `urllib` (`sources/oci.py`, no new dependency, no
  registry-kind assumptions so M13 can add push): `GET /v2/<repo>/manifests/<digest>` with
  `Accept: application/vnd.oci.image.manifest.v1+json` (bytes must hash to the pinned digest),
  then `GET /v2/<repo>/blobs/<layer digest>` with the digest checked while streaming.
- Anonymous bearer tokens: the `WWW-Authenticate` realm must match a fixed table of known
  registries (`ghcr.io` → `ghcr.io`; Docker Hub, named `docker.io` or `registry-1.docker.io` →
  `auth.docker.io`; `quay.io` → `quay.io`; `*.pkg.dev` → same host) or be the registry's own
  host; anything else is `SOURCE_INVALID`. The token request is built client-side: `scope` is
  always `repository:<repo>:pull` and `service` comes from the table (or the registry host),
  never echoed from the challenge.
- Redirects: at most 3, each hop `https`, each hop host re-checked as not private (§8.3), and the
  `Authorization` header dropped on any cross-host hop (blob storage redirects are pre-signed).
- Plain HTTP only when the registry host is an IP literal in `127.0.0.0/8` or `::1` (kind's
  `127.0.0.1:5001`), never the name `localhost`.
- Extraction (§8.2) only after the layer digest matches.

### 6.3 HTTP index

- `index.json`, pinned by `index_sha256` in `agent.yaml`:

  ```json
  {"index_version": 1,
   "versions": {"tool/geo@1.2.0": {"archive": "tool-geo-1.2.0.tar.gz",
                                    "archive_sha256": "sha256:…", "tree_sha256": "sha256:…"}}}
  ```

  `archive` is relative to the index URL; the resolved URL must be `https` on the index's host.
- Build: fetch the index, check `index_sha256` (`SOURCE_INDEX_MISMATCH`), fetch the archive,
  check `archive_sha256`, extract (§8.2), recompute the tree hash, which must equal
  `tree_sha256` (`SOURCE_INDEX_MISMATCH`). Because the index is pinned, the archive hashes in it
  are pinned too: no trust on first use beyond the pin you chose.
- `registry pack DIR --index index.json --base-url URL` writes the archives and the index for a
  static host, and prints the consumer line `index_sha256: sha256:…`.

### 6.4 Kinds refused from remotes

`runtime` and `service` refs that resolve only in a remote give `REMOTE_KIND_REFUSED`:
"runtimes and services come from ./registry, AGENTBREEDER_REGISTRY_PATH or the built-in
registry; copy it into ./registry with: agentbreeder registry import …" (M9a). A remote that
contains such versions is not an error; they are ignored for resolution and shown greyed.

Allowed from remotes (owner, D5, 2026-11-11): `prompt`, `eval`, `knowledge`, `tool`, `mcp` (§6.6)
and `agent`. The `agent` kind does not exist yet (`registry.py:36`); M13 (#763) defines it and its
spec must cover a remote `agent` version: what the package pins (image digest, lock, eval
result), where the image is pulled from (trust rules of §8.3 apply to that registry too), and the
consent before an agent from another source is deployed or called (M19).

### 6.6 Remote MCP versions

An `mcp` version is configuration, not code: an upstream URL, an auth type, secret imports and
tool effects (`builtin_registry/mcp/github/1.0.0/mcp.yaml`). It is resolved twice: by `build` for
the agent's `mcp:` entries (tool names checked, `build.py:181-230`) and by `services up` for the
stack's relay, which imports the version's secrets and sends them to its upstream
(`services/mcp_stack.py:98-108`, `relay_spec`). A remote version therefore chooses **which local
credential is sent where**, so:

- `stack.yaml` gains the same `registries:` block (§5 rules). Its sources may provide only `mcp`
  versions; anything else from them is `REMOTE_KIND_REFUSED`. `stack.lock` follows the agent.lock
  rule: v2 when `stack.yaml` declares `registries:`, v1 byte for byte otherwise (§7).
- The relay itself stays built-in only (`RELAY_RESERVED`, `mcp_stack.py:155-161`).
- **Origin and identity.** `mcp_stack.origin_of` (`mcp_stack.py:51-57`) maps `remote:<name>`
  to `remote:<canonical URL>@<full pin>` (§5 canonical form, full 40-hex commit or digest). That
  string goes into `identity()` (`mcp_stack.py:89-96`) and the grant digest (`grant_section`,
  `mcp_stack.py:341-366`), so neither the project-chosen source name nor a short SHA can keep an
  old consent valid. Short forms (`acme @3f2a9c1`) are for display only.
- **Consent bound to the pin.** Today `--allow-project-mcp SERVER` is keyed on the server name
  only (`services/engine.py:254-267`), and in plain mode an identity change only rewrites
  `lock.mcp` (`mcp_stack.py:197-207`). For a remote origin the flag must name the pin,
  `--allow-project-mcp SERVER@<full pin>`, so a flag left in a script stops authorising after a
  pin bump or an origin change; a missing or stale pin is `MCP_PROJECT_CONSENT`. The pin alone
  suffices because a commit or digest authenticates the bytes wherever they are served; for an
  HTTP source the pin is the full `index_sha256`. Tests: a stale `SERVER@pin` is refused after a
  pin bump, and a bare `SERVER` is refused for a remote origin.
  `needs_project_consent` (`mcp_stack.py:381-383`) fires for **every** remote origin, also with
  `auth: none`, because tool arguments still leave the machine.
- **Only referenced credentials.** `relay_spec` mounts every `secrets:` entry into the relay
  (`mcp_stack.py:292-298`), while consent lists only `auth.secret_names()` (`mcp_stack.py:100`)
  and `McpSpec` checks only that auth names are declared (`mcp.py:187-189`). For a remote origin,
  a secret that `auth` does not reference is refused (`MCP_INVALID`), so a remote version cannot
  import an extra host variable without a "will be sent to" line.
- **Consent text** names the source and full pin, and for `oauth_refresh` lists both the token-URL
  host (refresh credential) and the upstream (minted access token), not only the token host
  (`mcp_stack.py:106`): "credential GITHUB_MCP_TOKEN will be sent to https://api.githubcopilot.com;
  this MCP version comes from https://github.com/acme/agent-registry @3f2a9c1d…".
- **One version, one set of bytes.** `build` and `release` compare, for each `mcp` ref the agent
  uses, the `agent.lock` and `stack.lock` entries: same hash and same canonical source and pin,
  else `MCP_VERSION_MISMATCH`. When `stack.lock` is missing or has no entry for the ref, `release`
  fails closed and `build` warns. No such check exists today (grep of `services/`,
  `deployment.py`, `release.py`, 2026-11-11); the MCP slice adds it.
- Unchanged and relied on: upstreams are `https` on port 443 only (`mcp.py:69-79`,
  `mcp_stack.py:231`); the relay refuses private, loopback and link-local resolution
  (`relay.py:266-277`); the console refuses `services up` for stacks with MCP servers
  (`center/server.py:233-237`).
- The trusted-source list (§8.3) covers fetching the version; it does not cover the upstream the
  version names. Upstream egress stays governed as today (relay, grants, consent).

### 6.5 Same version, two hashes

Git cannot store empty directories, and the tree hash includes directory records
(`registry.py:246-247`). To keep one version hashing the same from a directory, a git commit or
an archive, M9b makes `publish` and `verify` refuse empty directories (`ENTRY_UNSAFE`, "add a
`.keep` file"; the scaffold already does, `registry.py:362`). No shipped version has an empty
directory today (checked 2026-11-11). Line-ending conversion (`core.autocrlf`) cannot happen
because blobs are read raw, never checked out. A *local checkout* of the same org repo placed on
`AGENTBREEDER_REGISTRY_PATH` can still differ (`core.autocrlf`, LFS smudge) and then gives a
`REF_CONFLICT`; the troubleshooting table says so and suggests using the remote source instead.

## 7. `agent.lock` format v2

v1 today (`lock.py`; `services/lock.py:42` loads `stack.lock` through the same `Lock.load`):

```json
{"lock_version": 1,
 "entries": {"tool/orders@1.0.0": {"sha256": "sha256:…", "source": "registry"}}}
```

`source` is `builtin`, a path relative to the project, or an absolute path.

v2:

```json
{
  "lock_version": 2,
  "sources": {
    "acme": {"type": "git", "url": "https://github.com/acme/agent-registry.git",
             "pin": {"commit": "3f2a9c1d…"}, "path": "registry"},
    "acme-images": {"type": "oci", "url": "ghcr.io/acme/agent-registry",
                    "pin": {"digest": "sha256:9b1e…"}},
    "community": {"type": "http", "url": "https://registry.example.org/agentbreeder/index.json",
                  "pin": {"index_sha256": "sha256:51d0…"}}
  },
  "entries": {
    "tool/orders@1.0.0": {"sha256": "sha256:…", "source": "registry"},
    "tool/geo@1.2.0": {"sha256": "sha256:…", "source": "remote:acme"},
    "prompt/greeter@2.0.0": {"sha256": "sha256:…", "source": "remote:community",
                             "pin": {"archive_sha256": "sha256:…"}}
  }
}
```

**When v2 is written.** Whenever `agent.yaml` declares `registries:` (even if every ref resolves
locally), with `sources` holding every declared source. Without `registries:`, the lock is v1,
byte for byte as today: Northwind, the starter template, the built-in fixtures and every existing
lock are untouched. Removing `registries:` writes v1 again.

**Reading: a strict schema in `Lock.load(path, versions=…)`.**

- Signature `Lock.load(path, versions=(1, 2), extra_keys=())`. `agent.lock` accepts `(1, 2)`
  and no extra top-level keys; `StackLock.load` passes `versions=(1, 2)` and
  `extra_keys=("imports", "mcp")` (`services/lock.py:66-76`). In `stack.lock`, `remote:` entries
  may only be `mcp` refs (§6.6). Both files are written v2 only when their YAML declares
  `registries:`. Northwind's `stack.lock` must load and re-save byte-identical (slice 1a test).
  Tests that pin today's constant: `tests/test_registry.py`, `tests/test_services_lock.py`,
  `tests/test_mcp_stack.py`.
- Unknown top-level keys, entry keys, `sources.*.type` values or `pin` keys: `LOCK_INVALID`
  ("written by a newer AgentBreeder; upgrade"). A `lock_version` above 2 gets the same message.
- v1: no `sources`; any `source` starting with `remote:` is `LOCK_INVALID`.
- v2: every `remote:<name>` must name a key in `sources`; every `sources` URL must pass the §5
  URL rules again (the lock is project-controlled input too). There are no per-entry URLs: an
  HTTP archive URL is re-derived from the pinned index, so a lock cannot point a fetch anywhere
  the index does not.
- `source` dispatch happens before any path join: `remote:` goes to the source layer, never
  `project_dir / source` (today's `lock.py:113-117`). A local label that would start with
  `remote:` is refused (`REGISTRY_ROOT_INVALID`).
- `sha256` keeps its meaning: `copy_and_hash` of the copy in the build context.
- **Per-entry pins** live in one `pin` object (HTTP: `archive_sha256`), so M13's digest-pinned
  `agent/…` entries can add pin fields without a v3.

**Behaviour.**

- `_reconcile` (`lock.py:155-184`) writes the whole entry from the `Root` (keeping `pin`), not
  `{"sha256", "source"}`.
- Plain build: a changed pin in `agent.yaml` replaces that source in `sources`; every entry from it
  is re-fetched and must keep its hash, else `HASH_MISMATCH` naming both pins (§12).
- `--locked`: `registries:` must equal `sources` exactly, else `LOCK_STALE` naming the difference
  (§12).
- `build.json` (`build.py:399-405`) gains the resolved `sources` table (redacted URLs, pins), so
  the build record and M13's package carry the pins, not only the labels.
- Consumers: `release._eval_set` (`release.py:74-78`) re-resolves through `registry.roots()` and
  would miss a remote eval set; it moves to one helper, `sources.locate(lock, entry, project)`,
  which `lock.py` calls (so `lock.py` imports no network code) and which applies the strict lock
  checks above. `promote._evidence` reads entries only; unchanged. `mcp_stack.origin_of`
  (`mcp_stack.py:51-57`) maps a `remote:` source to a remote origin with its pin (§6.6) instead
  of today's fallback label.

**Older AgentBreeder** reading v2 fails with `LOCK_INVALID: unsupported lock_version`; from M9b
on the message says to upgrade.

**Note for lane A (M3 #819, owns `lock.py` until M9b):** posted on #819. Do not change the lock
JSON shape or the meaning of `source`; keep reads through `Lock`/`Lock.load`; message changes
(LOCK_STALE wording, `lock.py:161`) are fine and M9b keeps them.

## 8. Cache, trust and pinning

### 8.1 Cache

- Location: `AGENTBREEDER_CACHE_DIR`, default `~/.agentbreeder/cache`; sources under
  `registries/v1/`. On every use the directory must be owned by the user and mode `0700` (as
  `operator_config.check_private_file`, `operator_config.py:380-390`), else `CACHE_UNSAFE`.
- Layout:
  - `trees/<tree-hex>/` one extracted version, by its tree hash; written to a temporary sibling
    and renamed into place;
  - `blobs/sha256/<hex>` OCI layers and HTTP archives, by digest;
  - `listings/<sha256(type, canonical url, pin, path)>` the source's listing at that pin: the
    set of ref **names** (from `ls-tree --name-only`, the OCI layer's member names, or the index),
    plus an optional tree hash per ref, filled in only when that ref is materialised for a build.
    Unrequested versions are never extracted or hashed, and an unsafe or oversized version that
    no build requests is never an error. The conflict check compares by hash, so a requested ref
    whose remote hash is missing is fetched and hashed (`SOURCE_OFFLINE` under `--offline`). A pin
    is immutable, so the listing only grows. Parsed strictly (ref regex, 64-hex hashes); any parse
    failure discards it and refetches. **Untrusted:** a planted listing can at worst hide or
    invent a `REF_CONFLICT`, hide one source's copy so a first lock resolves from a later declared
    source (still a trusted, pinned source, and the lock shows `remote:<name>`), or show a wrong
    state; it never supplies built bytes (first locks re-derive from the pinned object, locked
    builds check the lock's hash).
  - HTTP `index.json` bytes are stored in `blobs/` by `index_sha256`, so a cold `--locked`
    build re-derives archive URLs from the pinned index without trusting the lock for URLs.
- **When the cache may be used.**
  - `--locked` (and plain builds for refs already in the lock): `trees/<hash from agent.lock>`
    may be used; the build copies it into the context and the copy's hash must equal the lock
    (`HASH_MISMATCH` otherwise, after which the tree is deleted, `CACHE_CORRUPT`). The hash being
    checked comes from the committed lock, not from the cache.
  - **First lock of a ref** (no lock entry): never from `trees/`. The version is re-derived from
    the pin-authenticated object: a fresh git fetch at the pinned commit, or an OCI/HTTP blob whose
    bytes are re-hashed against the pinned digest. A self-consistent planted tree in the cache can
    therefore never become a first lock.
- Network only on a cache miss, so a locked build works offline once fetched. The one exception:
  the **first lock of a git ref always needs the network** (no git objects persist, and a first
  lock never reads `trees/`); `--offline` then gives `SOURCE_OFFLINE`. OCI and HTTP first locks
  can be offline when the pinned blob is cached (re-hashed against the pin).
- Concurrency: `fcntl.flock` on `registries/v1/.lock`, shared for builds and fetches, exclusive
  for `prune`. Every cache write (`trees/`, `listings/`, `blobs/`) goes to a temporary sibling
  and is renamed into place, so concurrent builds never see a partial file.
- Commands:
  - `agentbreeder registry fetch [AGENT_YAML] [--json]` fetches each declared source's listing
    and the trees of the agent's own refs that `agent.lock` already pins (verified against the
    lock), so a later `build --locked --offline` works. Refs not yet in the lock are reported
    ("not locked yet; run agentbreeder build") and not cached as trees. Used by CI before
    `--offline`, and by the console's Fetch action.
  - `--offline` on `build`, `release` and `eval`: never uses the network (`SOURCE_OFFLINE`
    naming the missing source and "run agentbreeder registry fetch"). `registry fetch --offline`
    is an error. `next` never fetches.
  - `agentbreeder registry cache list [--json]`; `agentbreeder registry cache prune [--older-than
    30d] [--dry-run]` removes trees and blobs not used for the given age (one rule; last-use time
    recorded on each use).

### 8.2 Safe extraction (OCI layers, HTTP archives)

`tarfile` with a manual member loop, never `extractall`. Only members under the requested
`kind/name/version/` prefixes are written; regular files and directories only (symlinks,
hardlinks, devices, FIFOs refused `ENTRY_UNSAFE`); names normalised, no absolute paths, no `..`,
UTF-8 only; archive-level caps of 50 000 members and 1 GB uncompressed, plus a 10:1 compression
ratio cap (gzip bombs); modes reduced to 0644/0755; files opened `O_EXCL | O_NOFOLLOW` in the
private staging root. `copy_and_hash` then runs on it exactly as for a local version.

### 8.3 Trusted sources

A fetch reaches only sources the **user** has trusted. The list lives in a user-level file,
`~/.agentbreeder/sources.yaml` (owner-only, checked like the operator config), never in the
project, never in `agent.yaml`, and not in the operator config (the guided path uses per-stack
demo operator configs, and `operator_config.py` belongs to lane E).

```yaml
trusted:
  - https://github.com/acme            # scheme + host[:port] + path prefix; no wildcards
  - ssh://git@github.com/acme
  - https://ghcr.io/acme
```

- Entries are `scheme://[user@]host[:port]/path-prefix`, canonicalised exactly like source URLs
  (§5), and match the canonical source URL on whole path segments; the ssh user must match
  exactly. A bare host (`https://github.com`) is accepted only with `--whole-host`, because it
  trusts every repository there, including an attacker's. The hint in `SOURCE_HOST_UNTRUSTED`
  proposes the first path segment (`https://github.com/acme`), or the full repo path when the
  path has one segment (`https://git.corp/registry.git`).
- **Checked inside the fetcher, on every network call, in every mode**: plain build, `--locked`
  with a cold cache (CI and the console's `build --locked`, `center/jobs.py:231`), `release`,
  `registry fetch`, `registry pin`. A planted project with a matching `agent.lock` therefore
  cannot make a build reach an untrusted host either.
- Hosts that resolve to loopback, RFC 1918, link-local or unique-local addresses are refused
  unless the entry carries `private: true` (as `Destination` does, `operator_config.py:273-274`),
  re-checked on every redirect hop (`SOURCE_HOST_PRIVATE`, §12). When the host resolves to a
  private address, the first `SOURCE_HOST_UNTRUSTED` hint already includes `--private`.
- DNS rebinding: `urllib` (OCI, HTTP) connects to the IP that passed the check; git over https
  gets `-c http.curloptResolve=<host>:<port>:<ip>` with the checked IP. Git over ssh cannot be
  pinned this way, and behind an `HTTPS_PROXY` the proxy resolves the target; the docs say the
  private-address check is best effort for ssh and behind a proxy. The trust-prefix check still
  holds in both cases.
- Proxy URLs may carry `user:pass@`: they are redacted in git stderr and `urllib` exception text,
  and proxy values are never printed in messages or `--json` output.
- `agentbreeder registry trust URL [--whole-host] [--private] [--yes]`, `registry trust --list`,
  `registry trust --remove URL`. Interactive confirm unless `--yes`; without a TTY and without
  `--yes` it exits 2 ("re-run with --yes"). It writes only the user-level file. The console shows
  the command to copy; it never offers a one-click trust.
- CI: `AGENTBREEDER_REGISTRY_TRUST` (comma-separated **prefix entries only**) is read in addition
  to the file; a bare host or a private host needs the file (`--whole-host`, `--private`). It is
  set by whoever runs CI, never read from the project; a test pins that no code path derives it
  from project files (today the CLI sets only `AGENTBREEDER_OPERATOR_CONFIG`, `cli.py:388`). The
  console passes `AGENTBREEDER_*` variables to its children (`center/jobs.py:59`); that is the
  owner's own environment. Without a TTY, `SOURCE_HOST_UNTRUSTED` adds "in CI set
  AGENTBREEDER_REGISTRY_TRUST=https://github.com/acme".
- What this does and does not do: it limits **which hosts a project can make your machine
  contact with your credentials** (including internal ones). It is not publisher verification:
  a trusted org prefix can still serve harmful code, and tools are code that runs inside the agent.

### 8.4 What protects you, and when

| Moment | What protects you | What does not |
|---|---|---|
| First lock of a ref (plain build) | the pin you chose; the trusted-source list; entry checks; first lock never from the cache | nothing vouches that the commit/image/archive is benign |
| Later builds, `--locked` | the tree hash in `agent.lock`, the source pin, the trusted-source list | |
| Remote changes (push, force-push, re-tag, index edit) | the pin and the lock; content is never read from a moving name | a deleted commit makes a cold-cache build fail (`SOURCE_COMMIT_MISSING`), not change |
| The cache | owner/mode check; locked hashes come from the lock; first lock bypasses it | |
| A ref removed from the lock and re-added | only the pin (it is a first lock again) | |

Git pins are SHA-1 commit ids; the content check after that is SHA-256 over the copied tree.

### 8.5 Console

- **Network-free reads.** The registry view runs `registry list` and `registry verify` as CLI
  children (`center/server.py:181-185`). Both gain `--agent AGENT_YAML` (to know the declared
  sources) and are network-free by construction: they read the cache only and never call the
  fetcher (a test asserts no socket and no `git` subprocess). Rows keep `ref`, `root`, `path`
  and gain `source: {name, type, url, pin}` and `state`. Selectors on `list`/`verify`:
  `--agent` defaults to `./agent.yaml` when present; `--source NAME` implies it; `--root DIR`
  lists only that directory and excludes both.
- **Only the Fetch action uses the network.** The console's build, release and rollback children
  (`center/jobs.py:231-235`) always pass `--offline`; a cold cache shows the *not fetched* state
  with the Fetch action instead of fetching inside a release. The state says which case it is:
  "fetch needed" (locked, not cached: Fetch fixes it) or "not locked yet" (a new remote ref or a
  pin bump: the console shows the command to copy, `agentbreeder build agent.yaml`, with "a
  first lock of a remote version runs in your terminal, online", then Fetch).
- The console child environment (`center/jobs.py:48-59`) adds `SSH_AUTH_SOCK` and the proxy
  variables, so ssh and proxied sources work from the console (lane B change in slice 3).
- **Source chip** per version: `acme · git · 3f2a9c1`; full URL and pin on hover; all remote text
  (file names, descriptions, git stderr) rendered with `textContent`, control characters
  stripped (S-27).
- **States:** *fetched*; *not fetched* (row greyed, no files or diff; action "Fetch" runs `registry
  fetch` as a CLI child under the project lock scope); *source not trusted* (shows the `registry
  trust` command to copy, no button); *conflict* (both roots side by side with hashes and a diff
  link); *refused kind* (greyed, with the §6.4 sentence).
- **Code-bearing confirm (S-05 parity).** Before the first Fetch of any remote source at a pin the
  project has not confirmed, the console shows "github.com/acme/agent-registry @3f2a9c1 may
  contain tools and eval sets: tool code runs inside your agents and an eval set decides the
  release gate" and needs a confirm. The confirmation is keyed on (canonical URL, pin) and
  stored per project in center state, like `accepted_grant` (`center/jobs.py:161`); a pin change
  asks again. Console build, release and rollback are also gated on that confirm for every
  declared (URL, pin), because the shared cache may already hold the trees (fetched by the CLI or
  another project). It stays a UX control, as S-05 says, not a security boundary. After the fetch, the console shows what the source actually provides. Projects
  registered in the console pass the S-01 ownership checks before any fetch.
- Remote versions are read-only: "Edit → new version" becomes "Copy into project as a new version"
  (M9a import).

### 8.6 Claims

Docs may say: "for refs already in `agent.lock`, a locked build uses exactly the recorded bytes;
a changed remote cannot change it" and "AgentBreeder fetches only from sources you trusted".
Docs must not say: signed, verified publisher, provenance, supply-chain secure, tamper-proof. As
today: "hashes detect a change after the first lock; they do not vouch for the first lock".

### 8.7 Evidence

Release and promote evidence (and `build.json`) record, per ref, the source name, type, redacted
URL and pin. Fetch refusals (`SOURCE_HOST_UNTRUSTED`) go to stderr only; they are not audit
events (no deploy happened).

## 9. Organization registry repo and agent repos

**Org registry repo** (e.g. `acme/agent-registry`, a template ships in the docs):

```
registry/tool/geo/1.2.0/manifest.yaml
registry/tool/geo/1.2.0/src/…
registry/prompt/support-base/1.0.0/…
.github/workflows/verify.yml
```

```yaml
# verify.yml (excerpt)
- uses: actions/checkout@v4
  with: {fetch-depth: 0}          # --against needs the base revision
- run: agentbreeder registry verify --root registry --against origin/main
```

An unknown revision gives `revision 'origin/main' not found; fetch it first`.

- Publish = `agentbreeder registry publish ./tool-geo --to registry`, commit, pull request,
  review, merge.
- `registry verify` and `registry list` gain `--root DIR`. `verify --against REV` (new) fails if
  any version present at `REV` was changed or removed in the working tree: the immutability check
  for the org repo.
- Protect the default branch (no force-push) and tag release commits, so pinned commits are never
  garbage-collected by the host.
- After merge, release notes carry the consumer snippet from
  `agentbreeder registry pin --git URL --to main --name acme`.

**Agent repo** (e.g. `acme/support-agent`): `agent.yaml` with the `registries:` block, its own
`./registry` for agent-specific versions, `agent.lock` committed. A pin bump is a one-line diff in
`agent.yaml` plus the lock diff; after a pin change `build` prints new versions in that source
(`new in acme: tool/geo@1.3.0`), and `registry list --source acme` lists them.

**CI recipe** (documented):

```
export AGENTBREEDER_REGISTRY_TRUST=https://github.com/acme
# internal host on a private address instead (env entries are prefix-only):
#   agentbreeder registry trust https://git.corp.example/acme --private --yes
# cache ~/.agentbreeder/cache between runs (actions/cache), keyed on agent.lock
agentbreeder registry fetch agent.yaml
agentbreeder build agent.yaml --locked --offline
```

Private org repos in CI: the runner's git credentials (a deploy key via `ssh-agent`, or a
credential helper with a token); never a token in the URL (refused, §5).

## 10. Standard library (D4, open)

**Today** the built-in registry (`builtin_registry/`, shipped in the wheel and checked against
`_content.json`) holds only infrastructure: `runtime/adk` (1.0.4–1.1.0), `mcp/github` and
`mcp/playwright` (1.0.0), and `service/` postgres-pgvector, litellm, phoenix, mcp-relay,
playwright-mcp and echo. It has **no prompts, tools or eval sets**. Northwind's tools live in its
example registry; of them only `tool/web-search` (Tavily through the operator's tools proxy) is
generic.

**Where additions could live:**

| Option | How users get it | For | Against |
|---|---|---|---|
| A. In the wheel (today's mechanism) | nothing to do; resolves offline | no network, no trust step | each addition needs an AgentBreeder release; grows the wheel |
| B. An official `agentbreeder/registry` git repo, consumed as a remote source (M9b) | one `registries:` entry + `registry trust https://github.com/agentbreeder` | versions ship independently of releases; dogfoods M9b | needs the network and a trust step; a repo to maintain and protect |
| C. Both | small core in the wheel, the rest in the repo | best of each | two places to look |

**Candidate first additions** (each only after an end-to-end run, recorded in the M9b
verification record):

- `tool/web-search` promoted from Northwind (generic; needs the tools proxy and a Tavily key);
- a generic `eval/` smoke set (answers, stays on topic, no tool errors), usable as a starting gate;
- more `mcp/` versions for Streamable HTTP servers (each needs its live check, as #748 did).

**Recommendation:** decide D4 after slice 1b, when remote sources have run end to end. Then choose
B or C, starting with `tool/web-search` and the eval smoke set. Until then, M9b ships no
standard-library additions, and slice 7 stays unscheduled.

## 11. Delivery slices

1. **1a: `Root` refactor and lock v2 read/write, no network.** Strict `Lock.load`, `StackLock`
   v1-only (with `imports`/`mcp`), `--locked` resolves from the locked root only and that root
   must be configured now, `sources.locate` (in 1a it handles local labels and raises
   `SOURCE_OFFLINE` for `remote:`), `_eval_set` fix, empty-directory refusal. Northwind's
   `agent.lock` and `stack.lock` load and re-save byte-identical; Northwind 61/61.
2. **1b: git source, cache, trusted sources, `registry fetch|pin|trust|cache`, `--offline`.** The
   acceptance tests: a tool from a separate git repo (a local `git http-backend` behind a loopback
   HTTPS server in tests, with a test CA passed through `GIT_SSL_CAINFO` (allowed in §6.1;
   `SSL_CERT_FILE` is not reliably honoured by macOS git) and a `private: true` trust entry in a
   test `sources.yaml`; a real GitHub repo in the
   verification run); a force-push after locking does not change a `--locked` build; a stopped
   server gives `SOURCE_UNREACHABLE`; an untrusted host gives `SOURCE_HOST_UNTRUSTED` in plain and
   `--locked` modes.
3. **Console** (lane B screen): source chip, states, Fetch, confirm (§8.5).
4. **Remote MCP versions** (§6.6): `stack.yaml` `registries:`, `stack.lock` v2, full-pin origin
   in identity and grant, `--allow-project-mcp SERVER@PIN`, unreferenced secrets refused,
   consent text, `MCP_VERSION_MISMATCH`. Acceptance: an `mcp` version from a
   separate git repo runs through the relay after consent; a pin bump asks for consent again.
5. **OCI** (+ `registry pack`, layout docs), verified against kind's local registry and one public
   registry.
6. **HTTP index** (+ `registry pack --index`).
7. **Standard-library additions** (D4, open).

Each slice: tests first (unit + CLI end to end as a subprocess), docs and CHANGELOG, the gate,
different-model review, owner approval. Git hardening (§6.1) gets a dedicated test per option
(planted `.git/config` in the project, `GIT_ALLOW_PROTOCOL`/`GIT_SSH_COMMAND` in the environment,
a redirecting server, a symlink and a submodule in the commit, an LFS pointer, a `-o…` host).

## 12. Messages and error codes

Every new error ends with a `Next:` line from the shared hint helper with the launcher prefix
(guided path §4, §9.2), not a hard-coded `agentbreeder …`. All exit 2 like other registry errors;
`--json` errors carry `code` and `source`.

| Code | Message (shape) |
|---|---|
| `SOURCE_INVALID` | `source 'acme': <rule>` (credentials in URL, transport, pathspec, layout) |
| `SOURCE_UNPINNED` | `source 'acme': commit 'main' is a branch; a full commit SHA is required.` Next: `registry pin acme --to main` |
| `SOURCE_HOST_UNTRUSTED` | `source 'acme' fetches from https://github.com/acme, which you have not trusted.` Next (if you trust it): `registry trust https://github.com/acme` |
| `SOURCE_HOST_PRIVATE` | `source 'acme': git.corp.example resolves to a private address (10.x). If this is your internal host:` Next: `registry trust https://git.corp.example/acme --private` (without a TTY: add `--yes`; the env variable cannot express `--private`) |
| `SOURCE_AUTH_FAILED` | `source 'acme' (git, github.com): authentication failed. AgentBreeder uses your own git credentials; check that 'git ls-remote <url>' works in this shell.` |
| `SOURCE_UNREACHABLE` | `source 'acme' (git, github.com): could not connect (<cause>). Already fetched? Re-run with --offline. Otherwise check the network or VPN. No longer used? Remove it from agent.yaml.` |
| `SOURCE_TIMEOUT`, `SOURCE_TOO_LARGE` | name, host, the limit |
| `SOURCE_COMMIT_MISSING` | `source 'acme': commit 3f2a9c1 is no longer on github.com/acme/agent-registry (deleted branch or force-push). Machines that already fetched it still build (--offline).` Next: `registry pin acme --to main` |
| `SOURCE_TOOL_MISSING` | `git` not found |
| `SOURCE_OFFLINE` | locked ref not cached: `source 'acme' is not in the cache.` Next: `registry fetch`. Ref not locked yet: `tool/geo@1.3.0 from source 'acme' is not locked yet; a first lock needs the network.` Next: `build` (without `--offline`) |
| `SOURCE_INDEX_MISMATCH` | which hash differed |
| `REMOTE_KIND_REFUSED` | §6.4 |
| `REGISTRY_ROOT_INVALID`, `CACHE_UNSAFE`, `CACHE_CORRUPT` (warning) | path and reason |
| `REF_CONFLICT` | `tool/geo@1.2.0 differs between ./registry (sha256:ab12…) and source 'acme' (git github.com/acme/agent-registry @3f2a9c1). Versions are immutable: delete the local copy to use acme's, or publish yours as a new version.` Next: `registry diff tool/geo@1.2.0 --source acme` |
| `HASH_MISMATCH` (pin bump) | `tool/geo@1.2.0 changed between acme@3f2a9c1 and acme@9d0e4b2. A published version must not change; ask the registry maintainers to publish 1.2.1, or keep the old pin.` |
| `LOCK_STALE` (`--locked`) | `registries: acme commit differs from agent.lock (3f2a9c1 → 9d0e4b2).` Next: `build`, then commit agent.lock |
| `REF_NOT_FOUND` | lists the roots searched, remotes by name |

`build` prints one line per remote ref: `tool/geo@1.2.0 ← acme @3f2a9c1`.
(`registry diff --source` depends on M8's `registry diff`.)

## 13. Cross-repo sync

- **OSS:** `registry.py` (`Root`, empty-directory refusal, `--root`, `--agent`), `lock.py` (lane C
  from M9b: strict v1/v2), new `sources/` package (`git.py`, `oci.py`, `http.py`, `cache.py`,
  `extract.py`, `trust.py`, `locate`), `agentfile.py` (`registries:`), `build.py`/`release.py`
  (resolution, `--offline`, `build.json` sources), `services/lock.py` and `services/schema.py`
  (`registries:` in `stack.yaml`, v1/v2), `services/mcp_stack.py` (remote origin, consent text,
  `MCP_VERSION_MISMATCH`), the registry command module, JSON Schema,
  `docs/registry.md` (claims §8.6, troubleshooting table code → remedy), `docs/agent-yaml.md`
  (`registries:` example), a new "Use a tool from your org registry" guide (trust, pin, build,
  commit the lock), the org-registry template, the CI recipe, CHANGELOG. `operator_config.py` is
  not touched (trust lives in its own file).
- **Cloud (`agentbreeder-cloud`):** grep (2026-11-11) found no `agent.lock` or `lock_version`
  use; no code change. Its `CLAUDE.md` records lock v2 and that any Cloud reader of `agent.lock`
  must accept v1 and v2 and refuse unknown keys.
- **Website:** nothing until R (#836); then `registry.mdx` and claims only for source types that ran.

## 14. Owner decisions

Decided by the owner on 2026-11-11:

- **D1 yes.** Trusted-source list, default empty, per user in `~/.agentbreeder/sources.yaml`,
  checked on every fetch in every mode, org-prefix entries (§8.3).
- **D2 yes.** Pins in YAML (commit, digest, index hash), no floating refs; `registry pin` resolves
  branches and tags (§5).
- **D3 yes.** Lock v2 written whenever the YAML declares `registries:`, v1 otherwise (§7).
- **D5 yes, extended.** Remote kinds: `prompt`, `eval`, `knowledge`, `tool`, **and `mcp`** (§6.6)
  **and `agent`** (once M13 defines it, §6.4). `runtime` and `service` stay refused.
- **D6 yes.** `--locked` resolves only from the locked (and currently configured) root (§4.3).
- **D7 yes.** Empty directories refused in registry versions (§6.5).

Open:

- **D4** Which standard-library additions, and where they live (§10 lists the options).

## 15. Plan delta (for the console-epic plan, at M9b start)

- Lanes: M9b slice 1b needs no lane E change (trust has its own file). Slice 3 is a lane B screen.
- M8 (`registry diff`) before `registry diff --source`.
- M13 inherits: `pin` objects in entries, `build.json` `sources`, `sources/oci.py` as the client
  to extend with push; `agent` joins `KINDS` (`registry.py:36`) in M13, not here.

## 16. Review log

- r1 (2026-11-11): draft.
- r1 reviews (Claude Fable, read-only, against the code): architecture (16 findings, CHANGES
  REQUESTED), UX (17, CHANGES REQUESTED), `/security` (16, CHANGES REQUESTED). Spot-checked against
  the code before folding: `--locked` resolves across all roots before redirecting
  (`lock.py:110-119`); `StackLock.load` delegates to `Lock.load` (`services/lock.py:42`);
  `_reconcile` drops extra entry keys (`lock.py:162-183`); the console builds with `--locked`
  (`center/jobs.py:231`) and passes `HOME` and `AGENTBREEDER_*` to children (`jobs.py:48-59`);
  registry views are CLI children (`center/server.py:181-185`); no shipped version has an empty
  directory.
- r2 (2026-11-11) folds them:
  - Architecture: v2 whenever `registries:` is declared (A1); `Lock.load(versions=)` and v1-only
    `StackLock` (A2); `Root` type and per-build staging roots (A3); `--locked` resolves only from
    the locked root (A4, D6); fallback refspecs (A5); no persistent bare repos, so no gc concern
    (A6); no URL credentials (A7); cache hint index and `flock`, one prune rule (A8); archive-level
    caps and requested members only, OCI `Accept` (A9); `--agent` on `list`/`verify` (A10);
    empty directories refused, LFS refused, `.pyc` skipped (A11); HTTP pinned by `index_sha256`
    (A12); `pin` objects, `build.json` sources, generic OCI client (A13); SHA-1 only (A14);
    `sources.locate`, `mcp_stack` refusal (A15); slice 1 split into 1a/1b (A16).
  - Security: trust checked on every fetch in every mode (S1); fresh bare repo per fetch, empty
    cwd, `--git-dir`, `GIT_CEILING_DIRECTORIES`, `GIT_*` scrubbed (S2); first lock never from
    `trees/`, cache owner/mode check (S3); strict lock schema, no per-entry URLs, `remote:`
    dispatch before path joins (S4); hardening options and `-`-prefixed hosts, literal pathspecs
    (S5); credentials refused in URLs (S6); prefix entries, `--whole-host`, private-address flag,
    narrowed wording (S7); OCI realm table, redirect and header rules, loopback by IP, size caps,
    exactly one layer (S8); download, time and size caps, bounded fallback (S9); HTTP pinned (S10);
    console code-bearing confirm and S-01 checks (S11); private staging root from authenticated
    objects (S12); sources in evidence (S13); qualified claims (S14); network-free `list`/`verify`
    (S15).
  - UX: `registry trust` (U2), `registry pin` (U4), env trust for CI and the CI recipe (U1),
    org-prefix entries (U3), `SOURCE_AUTH_FAILED` (U5), messages (U7–U9, U14, U15), provenance
    line per remote ref and `REF_NOT_FOUND` listing (U10), console states (U11), command shapes
    (U12), maintainer flow, `verify --against` example, `pack --index` (U13), `Next:` lines (U16),
    docs list (U17). **Not applied:** U6 (skip unreachable unused sources), reason in §4.2.
- r2 re-reviews (Claude Fable): architecture CHANGES REQUESTED (9: A1–A10, A12–A16 resolved), UX
  CHANGES REQUESTED (9: U1–U5, U7–U17 resolved; U6 rejection accepted if a listing cache
  exists), `/security` CHANGES REQUESTED (7: S1–S15 resolved except remainders below). The
  broken partial-clone fallback and the unconfigured locked label were found independently by two
  reviewers.
- r3 (2026-11-11) folds them:
  - Listing cache `listings/` per pin, untrusted, replaces the `index/` hint; plain builds online
    once per pin (arch r2-3, UX r2-1); `registry fetch` scoped to locked refs and listings, first
    git lock always online, HTTP index bytes in `blobs/` (arch r2-4).
  - `Lock.load(..., extra_keys=)`, StackLock keeps `imports`/`mcp` (arch r2-1).
  - Git fallback: full unfiltered fetch with refspecs; cap enforced by polling repo size and
    killing the process group (arch r2-2, sec r2-3).
  - Locked root must be configured now (arch r2-5, sec r2-4).
  - Proxy variables and `GIT_SSL_CAINFO` allowed; test CA via `GIT_SSL_CAINFO` (arch r2-6, r2-7).
  - autocrlf/LFS local-checkout note (arch r2-8); `sources.locate` in 1a (arch r2-9).
  - Canonical URL form shared by trust, lock and argv (sec r2-1).
  - Console: build/release/rollback always `--offline`; confirm on every new (URL, pin), stored
    per project (sec r2-2, UX r2-4); child env gains `SSH_AUTH_SOCK` and proxies (sec note).
  - Env trust is prefix-only, plus a test that no project path sets it (sec r2-5, UX r2-3).
  - DNS-rebinding pinning for urllib and git https; ssh best effort, documented (sec r2-6).
  - Token `scope`/`service` built client-side, `registry-1.docker.io`, `pin` ref validation
    (sec r2-7).
  - `SOURCE_HOST_PRIVATE` with one-step `--private` hint (UX r2-2); HTTP `pin` without `--to`
    and `pack` prints the consumer line (UX r2-5); trust hint prefix rules, ssh user matched
    exactly (UX r2-6 asked to ignore the user; security's exact match kept); non-TTY
    behaviour (UX r2-7); `list` selectors (UX r2-8); `fetch-depth: 0` and unknown-revision
    message (UX r2-9).
- r3 re-reviews (Claude Fable): UX **APPROVED** (3 Low wording fixes), architecture **APPROVED**
  conditional on one Medium fix, `/security` **APPROVED** (4 Low). All applied in r3 final:
  `SOURCE_OFFLINE` "not locked yet" variant, console "fetch needed" / "not locked yet" states, CI
  line for private hosts (UX); listing = ref names plus tree hashes filled on materialisation,
  unrequested versions never extracted or errors; atomic writes for all cache files (arch);
  proxy weakens DNS pinning (documented), proxy credential redaction, listing "at worst" case and
  strict parse, console build/release gated on the per-(URL, pin) confirm (security). Converged.
  Open for implementation: the slice 1b git spike (fetch-by-SHA fallback, `curloptResolve`,
  size polling) before code.
