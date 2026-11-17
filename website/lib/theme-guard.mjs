// The stored-theme allow-list and guard (design v2 §10.2, §11). Plain JavaScript so
// tests/test_website_theme_guard.py can run the guard in Node without a TypeScript step;
// lib/theme.ts re-exports it with types.

export const THEMES = /** @type {const} */ (['system', 'light', 'dark']);

// next-themes' storage key (its default; RootProvider does not change it).
export const THEME_STORAGE_KEY = 'theme';

// Runs before next-themes' blocking script. next-themes passes the stored value to
// classList.add, so a value with a space threw after hydration and blanked the page, and any
// other value became a class on <html>. Its `value` map does not help with a prototype key
// ("constructor", "__proto__"): the lookup returns a function or object, which then throws.
// The guard:
// - drops a stored value outside THEMES, so next-themes falls back to its default (`system`);
// - stops a `storage` event from another tab that carries such a value before next-themes'
//   listener (registered later, at hydration) sees it, and drops the value. The other tabs then
//   get a null value and reset to `system`.
// The value is only compared, never written into the page. Built from THEMES and the key with
// JSON.stringify: no user input reaches the script text.
export const THEME_GUARD_SCRIPT =
  `(function(){var k=${JSON.stringify(THEME_STORAGE_KEY)},t=${JSON.stringify(THEMES)};` +
  `function bad(v){return v!==null&&t.indexOf(v)<0}` +
  `try{if(bad(localStorage.getItem(k)))localStorage.removeItem(k)}catch(e){}` +
  `window.addEventListener('storage',function(e){if(e.key===k&&bad(e.newValue)){` +
  `e.stopImmediatePropagation();try{localStorage.removeItem(k)}catch(x){}}},true)})()`;
