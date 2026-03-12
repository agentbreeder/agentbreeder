# /docs — Documentation Sync

You are a documentation engineer. Detect code changes, update corresponding docs, remove stale references, validate quality.

## Do NOT ask for permission — just update docs.

## This skill does NOT run tests or lint. Those are separate skills (/tests, /lint).

## Step 1: Detect What Changed

```bash
git diff --name-only HEAD 2>/dev/null || true
git diff --name-only --cached 2>/dev/null || true
git ls-files --others --exclude-standard 2>/dev/null || true
```

If no code changes detected, skip to Step 4.

## Step 2: Map Changes to Docs

| Changed files | Doc to update |
|---|---|
| `cli/commands/*.py`, `cli/main.py` | `docs/cli-reference.md` |
| `api/routes/*.py` | `docs/api-reference.md` (create if missing) |
| `engine/config_parser.py` | `docs/agenthub-yaml.md` |
| `engine/**` (deployers, runtimes, sidecar) | `ARCHITECTURE.md` |
| `pyproject.toml`, `docker-compose.yml`, `.env.example` | `docs/quickstart.md`, `docs/local-development.md` |
| New features / major changes | `README.md` |
| Removed files / features | Remove stale references in all docs |

For each affected doc: read the current doc, read the changed source, update the doc.

## Step 3: Update Documentation

### Standards
- GitHub-flavored Markdown
- Code blocks must have language tags (```bash, ```python, ```yaml)
- Every CLI command needs at least one usage example
- Every API endpoint needs request/response examples
- Direct voice, second-person ("you"), imperative for instructions
- No emojis unless already present in the file
- Tables for structured data (options, env vars, fields)

### CLI Reference (`docs/cli-reference.md`)
- Read every command in `cli/commands/*.py` and `cli/main.py`
- Document: name, description, arguments, options, flags, examples
- Use `garden <command> --help` output as source of truth
- Include `--json` output format examples where applicable
- Remove docs for deleted commands, add docs for new commands

### API Reference (`docs/api-reference.md`)
- Read every route in `api/routes/*.py`
- Document: method, path, description, request body, response shape, auth
- Follow API conventions from CLAUDE.md (standard response envelope)
- Remove deleted endpoints, add new ones

### YAML Schema (`docs/agenthub-yaml.md`)
- Read `engine/config_parser.py` for Pydantic models and enums
- Document every field: name, type, required/optional, default, description
- Include a complete example `agent.yaml`

### Architecture (`ARCHITECTURE.md`)
- Update if deploy pipeline, sidecar, runtime, or deployer interfaces changed
- Keep system diagrams accurate

### README.md
- Update feature list, install instructions, command examples as needed

### Quickstart & Local Dev (`docs/quickstart.md`, `docs/local-development.md`)
- Update if setup steps, prerequisites, or env vars changed

### Stale Reference Removal
- Search all doc files for references to deleted/renamed functions, commands, endpoints, file paths, env vars
- Remove or update any stale references found

## Step 4: Validate

1. **Link check** — verify internal file references point to existing files:
```bash
grep -rn '\[.*\](\.\.*/\|docs/' docs/ README.md ARCHITECTURE.md CONTRIBUTING.md 2>/dev/null || true
```

2. **Code block check** — no unclosed code blocks:
```bash
for f in docs/*.md README.md ARCHITECTURE.md; do
  [ -f "$f" ] || continue
  count=$(grep -c '```' "$f" 2>/dev/null || echo 0)
  if [ $((count % 2)) -ne 0 ]; then echo "WARNING: $f has unclosed code block"; fi
done
```

3. **Freshness check** — confirm docs match current code (CLI help, route list, YAML fields).

## Step 5: Stage Doc Files Only

```bash
git add docs/*.md README.md ARCHITECTURE.md CONTRIBUTING.md SECURITY.md 2>/dev/null
git diff --cached --stat
```

## Output

```
=== Docs Summary ===
Changed code:  [list of changed source files]
Docs updated:  [list of doc files modified]
Docs created:  [list of new doc files]
Docs removed:  [list of removed references]
Validation:    Links OK, code blocks OK, freshness OK
Status:        PASSED | NO CHANGES NEEDED
```
