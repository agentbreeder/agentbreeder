# /security — Vulnerability & Secret Scan + Type Check

You are a security engineer. Scan for dependency vulnerabilities, leaked secrets, and type errors.

## Do NOT ask for permission — scan everything, fix what you can, report the rest.

## Step 1: Dependency Vulnerability Scan

```bash
./venv/bin/pip-audit 2>/dev/null || (./venv/bin/pip install pip-audit && ./venv/bin/pip-audit)
```

**Action on findings:**
- **Critical / High:** MUST fix — upgrade in `pyproject.toml`, run `./venv/bin/pip install -e ".[dev]"`, re-audit. Do not proceed until resolved.
- **Medium:** Fix if straightforward (< 5 min), otherwise note in output.
- **Low / Info:** Note but do not block.

## Step 2: Secret Scan

Search for leaked secrets in tracked files:
```bash
grep -rn "sk-proj-\|sk-ant-\|AKIA\|ghp_\|gho_" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.yaml" --include="*.yml" --include="*.json" --include="*.md" --include="*.env*" . | grep -v node_modules | grep -v venv | grep -v __pycache__ | grep -v ".claude/" || echo "No secrets found"
```

**Action on findings:**
- Ignore test fixtures (e.g. `"sk-proj-testkey1234"` in test files) — these are not real secrets.
- Remove any real secrets immediately from source code.
- Replace with environment variable reference or placeholder.
- Add the file to `.gitignore` if it should never be committed.
- NEVER leave real API keys, tokens, or credentials in source.

## Step 3: Python Type Check (non-blocking)

```bash
./venv/bin/mypy . --ignore-missing-imports 2>&1 || true
```

- Report error count. Fix critical type errors if straightforward.
- This step is informational — does not block.

## Step 4: TypeScript Type Check (non-blocking, if applicable)

Only if `dashboard/` has a `tsconfig.json`:
```bash
cd dashboard && npx tsc --noEmit 2>&1 || true
```

- Report error count. Does not block.

## Output

```
=== Security Summary ===
Dependency vulns: X found, Y fixed, Z noted
Secrets found:    X (all removed | none | test fixtures only)
Python types:     X errors (non-blocking)
TS types:         X errors (non-blocking)
Status:           PASSED | BLOCKED (if critical vulns or real secrets remain)
```
