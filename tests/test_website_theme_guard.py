"""Stored-theme guard on the website (design v2 #1113 §10.2, §11; step W #1115).

next-themes passes the stored theme to `classList.add`: a value with a space threw after hydration
and blanked the page, any other value became a class on <html>, and a prototype key
("constructor") arriving from another tab made its `value` map return a function, which threw.
`THEME_GUARD_SCRIPT` (website/lib/theme-guard.mjs) runs before next-themes and makes those values
harmless. These tests run the guard in Node against a fake `localStorage` and `window` (skipped
without Node); the browser check is the Playwright run recorded in
docs/superpowers/verification/design-v2-W.md. The layout checks are tripwires on the wiring only.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / "website"
GUARD_MODULE = WEBSITE / "lib" / "theme-guard.mjs"
LAYOUT = (WEBSITE / "app" / "layout.tsx").read_text()

TAMPERED = [
    "x y",
    "<img src=x onerror=alert(1)>",
    "",
    "a" * 100_000,
    "dark ",
    "foo",
    "system light",
    "constructor",
    "__proto__",
    "toString",
]
VALID = ["system", "light", "dark"]

# Loads the guard module, then runs its script text (as the browser would) once per case in a
# fresh sandbox, and prints what happened as JSON.
HARNESS = r"""
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const [modulePath, casesJson] = process.argv.slice(1);
import(pathToFileURL(modulePath).href).then((mod) => {
  const { THEME_GUARD_SCRIPT, THEMES, THEME_STORAGE_KEY } = mod;
  const out = { themes: THEMES, key: THEME_STORAGE_KEY, cases: [] };
  for (const c of JSON.parse(casesJson)) {
    const store = new Map();
    if (c.stored !== null) store.set('theme', c.stored);
    const listeners = [];
    const localStorage = c.throws
      ? { getItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } }
      : {
          getItem: (k) => (store.has(k) ? store.get(k) : null),
          removeItem: (k) => { store.delete(k); },
        };
    const window = {
      addEventListener: (type, fn, capture) => listeners.push({ type, fn, capture }),
    };
    let error = null;
    try {
      vm.runInNewContext(THEME_GUARD_SCRIPT, { localStorage, window });
    } catch (e) {
      error = String(e);
    }
    let stopped = null;
    if (c.event !== undefined && listeners.length) {
      stopped = false;
      store.set('theme', c.event);
      const ev = {
        key: c.eventKey ?? 'theme',
        newValue: c.event,
        stopImmediatePropagation() { stopped = true; },
      };
      listeners[0].fn(ev);
    }
    out.cases.push({
      error,
      stored: store.has('theme') ? store.get('theme') : null,
      listeners: listeners.map((l) => ({ type: l.type, capture: l.capture })),
      stopped,
    });
  }
  console.log(JSON.stringify(out));
});
"""


def _run(cases: list[dict]) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    proc = subprocess.run(
        [node, "-e", HARNESS, str(GUARD_MODULE), json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(proc.stdout)


def test_allow_list_and_key() -> None:
    out = _run([])
    assert out["themes"] == VALID
    assert out["key"] == "theme"  # next-themes' default; layout.tsx must not set another
    assert "storageKey" not in LAYOUT


@pytest.mark.parametrize("value", TAMPERED, ids=lambda v: repr(v[:20]))
def test_tampered_stored_value_is_removed(value: str) -> None:
    (case,) = _run([{"stored": value}])["cases"]
    assert case["error"] is None
    assert case["stored"] is None  # next-themes then falls back to `system`


@pytest.mark.parametrize("value", [*VALID, None])
def test_valid_or_missing_value_is_kept(value: str | None) -> None:
    (case,) = _run([{"stored": value}])["cases"]
    assert case["error"] is None
    assert case["stored"] == value
    assert case["listeners"] == [{"type": "storage", "capture": True}]


@pytest.mark.parametrize("value", TAMPERED, ids=lambda v: repr(v[:20]))
def test_tampered_value_from_another_tab_is_stopped(value: str) -> None:
    (case,) = _run([{"stored": "dark", "event": value}])["cases"]
    assert case["stopped"] is True  # next-themes' listener never sees it
    assert case["stored"] is None


@pytest.mark.parametrize("value", [*VALID, None])
def test_valid_value_from_another_tab_passes(value: str | None) -> None:
    (case,) = _run([{"stored": "dark", "event": value}])["cases"]
    assert case["stopped"] is False
    assert case["stored"] == value


def test_other_keys_pass() -> None:
    (case,) = _run([{"stored": "dark", "event": "x y", "eventKey": "other"}])["cases"]
    assert case["stopped"] is False


def test_blocked_storage_does_not_throw() -> None:
    (case,) = _run([{"stored": None, "throws": True}])["cases"]
    assert case["error"] is None
    assert case["listeners"] == [{"type": "storage", "capture": True}]


def test_layout_renders_guard_before_next_themes_with_value_map() -> None:
    guard = LAYOUT.find("__html: THEME_GUARD_SCRIPT")
    provider = LAYOUT.find("<RootProvider")
    assert -1 < guard < provider, "the guard must render before RootProvider (next-themes' script)"
    assert "value: { light: 'light', dark: 'dark' }" in LAYOUT
