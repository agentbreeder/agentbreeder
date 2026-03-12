# /lint — Lint & Format

You are a code quality engineer. Run linting and formatting, fix all errors, zero errors before done.

## Do NOT ask for permission — just fix everything.

## Execution

### Step 1: Auto-fix with ruff
```bash
./venv/bin/ruff check . --fix
```

### Step 2: Format
```bash
./venv/bin/ruff format .
```

### Step 3: Check remaining errors
```bash
./venv/bin/ruff check .
```

### Step 4: Fix remaining errors manually

For each error ruff could not auto-fix, read the file and fix it. Common patterns:
- `E501` (line too long) — break the line
- `B904` (raise from) — add `from err` or `from None`
- `UP042` (str+Enum) — use `enum.StrEnum`
- `F841` (unused variable) — remove it
- `C408` (unnecessary dict call) — use dict literal
- `B017` (blind exception) — use specific exception type

### Step 5: Re-run until clean
```bash
./venv/bin/ruff check .
./venv/bin/ruff format --check .
```

Repeat Steps 3-5 until both commands report zero errors.

## Scope

- Python files only: `./venv/bin/ruff` (config in `pyproject.toml`)
- Excludes are configured in `pyproject.toml` (`extend-exclude`)
- Dashboard TypeScript: `cd dashboard && npm run lint` (only if dashboard has changes)

## Output

```
=== Lint Summary ===
Errors found:    X
Auto-fixed:      Y
Manually fixed:  Z
Remaining:       0
Format:          Clean
Status:          PASSED
```
