# Studio Phase 6 — Quickstart CLI Streamlining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `agentbreeder quickstart`'s model-setup step calm and honest: when local Ollama is detected, default to "local, no key"; offer ONE optional gateway key (OpenRouter) with direct-provider keys behind an opt-in; and turn the Ollama rebind into an explicit opt-in step that reports failure clearly instead of silently falling through.

**Architecture:** All in `cli/commands/quickstart.py`. `_ask_model_source()` defaults to Local when Ollama+models are detected. `_collect_provider_keys()` leads with OpenRouter and gates OpenAI/Anthropic/Google behind a single "add direct provider keys? [y/N]". The rebind block in `_ensure_ollama()` flips to default-skip (`[y/N]`), and on failure (or skip) sets/returns a clear "local Ollama not reachable from the Docker stack — agents fall back to cloud" consequence rather than continuing as if fine.

**Tech Stack:** Python (Typer/Rich CLI), pytest with mocked `console.input`.

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `cli/commands/quickstart.py` — `_ask_model_source` (~1228), `_collect_provider_keys` (~1431) + `_CLOUD_PROVIDERS` (~1027), the rebind block in `_ensure_ollama` (~1356-1392).
- `tests/unit/test_quickstart_model_source.py`, `tests/unit/test_quickstart_assume_yes.py` — extend; add a rebind test module.
- `website/content/docs/quickstart.mdx` — update the model-setup section (~52-67).

---

### Task 1: Default to Local when Ollama is detected

**Files:** Modify `cli/commands/quickstart.py` (`_ask_model_source` ~1228-1275); Test `tests/unit/test_quickstart_model_source.py`

- [ ] **Step 1** — Read `_ask_model_source()` and how it learns whether Ollama is running/has models (it can call `_ollama_running()`/`_ollama_models()` or be passed that info). Confirm its return contract (the 1/2/3 choice → a source enum/string + the `skip_cloud_keys` behavior).
- [ ] **Step 2: failing test** — add to `test_quickstart_model_source.py`: when Ollama is detected with ≥1 model, pressing Enter (empty input) selects **Local** (the default), so no cloud-key prompts follow. When Ollama is NOT detected, the default remains the existing behavior. Mock `console.input` returning `""` and the ollama-detection helpers.
- [ ] **Step 3** — Implement: when Ollama is detected with models, mark the Local option as the default (empty-input → Local) and word the panel so "press Enter to use your local models (free, no key)" is the obvious path. Don't remove the cloud/gateway options.
- [ ] **Step 4** — run the model-source tests → green. `ruff check cli/commands/quickstart.py`, `ruff format --check`, `mypy cli/commands/quickstart.py --ignore-missing-imports` clean.
- [ ] **Step 5** — commit: `git commit -m "feat(cli): quickstart defaults to local models when Ollama is detected"`

---

### Task 2: Lead with one gateway key; gate direct providers

**Files:** Modify `cli/commands/quickstart.py` (`_collect_provider_keys` ~1431, `_CLOUD_PROVIDERS` ~1027); Test `tests/unit/test_quickstart_assume_yes.py`

- [ ] **Step 1** — Read `_collect_provider_keys()` + `_CLOUD_PROVIDERS`. Keep `_CLOUD_PROVIDERS` as the data, but restructure the prompt flow: (1) always offer **OpenRouter** first, framed as "one key → 100+ models (recommended)"; (2) then a single gate: "Add direct provider keys (OpenAI / Anthropic / Google)? [y/N]" — only if `y` do the existing per-provider prompts run; (3) keep the "already set → Enter to keep" behavior and the `--yes`/non-TTY early exit. Storage (`_write_env_key` → cwd/.env) unchanged.
- [ ] **Step 2: failing test** — extend `test_quickstart_assume_yes.py` (or a new `test_quickstart_provider_keys.py`): (a) declining the direct-providers gate (input `n`/Enter) prompts ONLY OpenRouter, not the other three; (b) accepting (`y`) prompts all; (c) `--yes` still skips all. Mock `console.input` sequences.
- [ ] **Step 3** — Implement the restructured flow.
- [ ] **Step 4** — run provider-key tests → green; ruff + mypy clean.
- [ ] **Step 5** — commit: `git commit -m "feat(cli): quickstart leads with OpenRouter, gates direct provider keys"`

---

### Task 3: Make the Ollama rebind opt-in and fail honestly

**Files:** Modify `cli/commands/quickstart.py` (rebind block in `_ensure_ollama` ~1356-1392); Test new `tests/unit/test_quickstart_ollama_rebind.py`

- [ ] **Step 1** — Read the rebind block + `_rebind_ollama_all_interfaces()` (~1108-1166) + `_ollama_bind_is_localhost_only()` (~1064-1105). Note the current bug: on rebind failure it `_warn`s then falls through with no flag/return.
- [ ] **Step 2: failing tests** (`test_quickstart_ollama_rebind.py`):
  - when bind is localhost-only and the user declines (default skip), the function does NOT attempt a rebind and prints a clear consequence ("local Ollama won't be reachable from the Docker stack — agents fall back to cloud");
  - when the user accepts and the rebind helper returns False, the flow surfaces the failure + manual commands AND records that local Ollama is not container-reachable (assert via a returned flag/state or an emitted message), not a silent fall-through;
  - when the rebind succeeds, no warning.
  Mock `_ollama_bind_is_localhost_only`, `_rebind_ollama_all_interfaces`, and `console.input`.
- [ ] **Step 3** — Implement: change the prompt default to **skip** (`[y/N]`); reword to explain WHY (containers reach Ollama via `host.docker.internal`, so a 127.0.0.1-only bind is invisible to the Docker stack) and that skipping is fine if you'll use cloud/gateway models. On skip OR failure, set a local `ollama_container_reachable = False` (or equivalent) and print the consequence once; do not pretend success. Keep the manual-commands hint on failure.
- [ ] **Step 4** — run the rebind tests → green; ruff + mypy clean.
- [ ] **Step 5** — commit: `git commit -m "fix(cli): Ollama rebind is opt-in and reports failure instead of silent fall-through"`

---

### Task 4: Docs + verify

**Files:** Modify `website/content/docs/quickstart.mdx` (~52-67)

- [ ] **Step 1** — Update the model-setup section: describe the Ollama-detected-→-Local default (free, no key), the single recommended OpenRouter prompt with direct keys behind an opt-in, and the rebind as an explicit optional step (with the host.docker.internal explanation + that skipping falls back to cloud). Remove any wording implying four separate cloud-key prompts.
- [ ] **Step 2** — Run the full quickstart test set: `venv/bin/python -m pytest tests/unit -k quickstart -v` (green). `ruff check cli/commands/quickstart.py`, `ruff format --check cli/commands/quickstart.py`, `mypy cli/commands/quickstart.py --ignore-missing-imports` (clean).
- [ ] **Step 3** — commit: `git commit -m "docs(quickstart): streamlined model setup + opt-in Ollama rebind"`

---

## Self-Review

**Spec coverage (§F):** local-no-key default when Ollama detected (Task 1) ✓; one optional gateway key, direct providers gated (Task 2) ✓; Ollama rebind explicit/opt-in + no silent failure (Task 3) ✓; docs synced (Task 4) ✓.

**Placeholder scan:** each task names the exact function + line range and the precise behavior change + the test cases (mock `console.input` sequences). No "improve the prompts" vagueness.

**Type/name consistency:** `_ask_model_source`, `_collect_provider_keys`, `_CLOUD_PROVIDERS`, `_ensure_ollama`, `_rebind_ollama_all_interfaces`, `_ollama_bind_is_localhost_only` are the actual symbols (from research); referenced consistently across tasks.
