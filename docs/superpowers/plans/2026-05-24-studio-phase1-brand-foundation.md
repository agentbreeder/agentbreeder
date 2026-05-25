# Studio Phase 1 — Brand Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AgentBreeder Studio dark-only and add the Bricolage Grotesque display font + marketing type scale, so Studio matches agentbreeder.io's brand.

**Architecture:** Studio already mirrors the website's color tokens in `dashboard/src/index.css`. This phase (1) drops the light theme so `.dark` is the only theme, removing the theme toggle; (2) adds Bricolage as a `--font-display` token with the website's marketing type scale; (3) introduces a shared `<PageTitle>` component that renders titles in the display font, adopted on Home and Models as the pattern.

**Tech Stack:** React + TypeScript + Vite + Tailwind v4 (`@theme` in CSS), `@fontsource-variable/*`, Vitest + Testing Library (component), Playwright (E2E).

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic is implemented and local tests pass).

---

## File Structure

- `dashboard/package.json` — add `@fontsource-variable/bricolage-grotesque` dependency.
- `dashboard/src/index.css` — import Bricolage; remove `:root` light tokens; add `--font-display` + `--text-display/-h1/-h2` tokens to `@theme inline`.
- `dashboard/index.html` — hardcode `<html class="dark">` so dark is always on before JS.
- `dashboard/src/hooks/use-theme.ts` — collapse to dark-only (no light/system).
- `dashboard/src/components/shell.tsx` — remove the theme-toggle control + `Sun`/`Moon`/`Monitor` imports.
- `dashboard/src/components/page-title.tsx` — NEW shared display-font page title.
- `dashboard/src/components/page-title.test.tsx` — NEW Vitest component test.
- `dashboard/src/pages/home.tsx`, `dashboard/src/pages/models.tsx` — adopt `<PageTitle>`.
- `dashboard/tests/e2e/brand-foundation.spec.ts` — NEW Playwright E2E for dark-only + display font.

---

### Task 1: Add the Bricolage display font

**Files:**
- Modify: `dashboard/package.json` (dependencies)
- Modify: `dashboard/src/index.css:1-4` (imports)

- [ ] **Step 1: Install the font package**

Run: `cd dashboard && npm install @fontsource-variable/bricolage-grotesque`
Expected: `package.json` gains `"@fontsource-variable/bricolage-grotesque": "^5.x"` and `package-lock.json` updates.

- [ ] **Step 2: Import it alongside Geist**

In `dashboard/src/index.css`, add the import after the existing Geist import (line 4):

```css
@import "@fontsource-variable/geist";
@import "@fontsource-variable/bricolage-grotesque";
```

- [ ] **Step 3: Verify it resolves (build)**

Run: `cd dashboard && npm run build`
Expected: build succeeds with no "Can't resolve '@fontsource-variable/bricolage-grotesque'" error.

- [ ] **Step 4: Commit**

```bash
git add dashboard/package.json dashboard/package-lock.json dashboard/src/index.css
git commit -m "feat(studio): add Bricolage Grotesque display font"
```

---

### Task 2: Add display-font + marketing type-scale tokens

**Files:**
- Modify: `dashboard/src/index.css` (the `@theme inline` block, near `--font-sans` ~line 87)

- [ ] **Step 1: Add the tokens**

In `dashboard/src/index.css`, inside `@theme inline`, directly after `--font-sans: 'Geist Variable', sans-serif;` add:

```css
    --font-display: 'Bricolage Grotesque Variable', 'Geist Variable', sans-serif;

    /* Marketing type scale — mirrors website/tailwind.config.ts */
    --text-display: 3rem;
    --text-display--line-height: 1.1;
    --text-display--letter-spacing: -0.03em;
    --text-h1: 2.25rem;
    --text-h1--line-height: 1.15;
    --text-h1--letter-spacing: -0.025em;
    --text-h2: 1.75rem;
    --text-h2--line-height: 1.2;
    --text-h2--letter-spacing: -0.02em;
```

- [ ] **Step 2: Verify utilities generate (build)**

Run: `cd dashboard && npm run build`
Expected: build succeeds. (`font-display`, `text-display`, `text-h1`, `text-h2` are now valid Tailwind utilities.)

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/index.css
git commit -m "feat(studio): add --font-display token + marketing type scale"
```

---

### Task 3: Make Studio dark-only

**Files:**
- Modify: `dashboard/index.html` (the `<html>` tag)
- Modify: `dashboard/src/hooks/use-theme.ts` (collapse to dark)
- Modify: `dashboard/src/components/shell.tsx:8-9,137-171` (remove toggle + icon imports)
- Modify: `dashboard/src/index.css` (remove `:root` light token block, lines ~9-43)

- [ ] **Step 1: Hardcode dark on the html element**

In `dashboard/index.html`, change the `<html ...>` opening tag to include the class:

```html
<html lang="en" class="dark">
```

- [ ] **Step 2: Collapse the theme hook to dark-only**

Replace the entire contents of `dashboard/src/hooks/use-theme.ts` with:

```ts
import { useEffect } from "react";

// Studio is dark-only, matching agentbreeder.io. Kept as a hook so existing
// imports keep working; it simply guarantees the `dark` class is present.
export function useTheme() {
  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);
  return { theme: "dark" as const, resolved: "dark" as const, setTheme: () => {} };
}
```

- [ ] **Step 3: Remove the theme-toggle UI from the shell**

In `dashboard/src/components/shell.tsx`:
- Remove `Moon,` and `Sun,` from the lucide-react import (lines ~8-9) and remove `Monitor` from that import if present.
- Remove the theme-toggle block: the `const { theme, setTheme } = useTheme();` line (~137), the `next`/`Icon` derivation (~139-142), and the toggle button/menu markup that renders `Theme: {theme}` and the `<span className="capitalize">{theme}</span>` (~158-171). Delete the now-unused `useTheme` import if nothing else uses it.

After editing, confirm no dangling references:
Run: `cd dashboard && npx tsc --noEmit`
Expected: no "Cannot find name 'Moon'/'Sun'/'Monitor'/'setTheme'/'theme'" errors.

- [ ] **Step 4: Remove the light token block from index.css**

In `dashboard/src/index.css`, delete the entire light-mode `:root { ... }` block (the one beginning with the `/* Light mode — map to neutral palette ... */` comment, ~lines 9-43). Keep the `.dark { ... }` block (it's now the only theme).

- [ ] **Step 5: Verify dark-only via build + typecheck**

Run: `cd dashboard && npm run build && npx tsc --noEmit`
Expected: both succeed.

- [ ] **Step 6: Commit**

```bash
git add dashboard/index.html dashboard/src/hooks/use-theme.ts dashboard/src/components/shell.tsx dashboard/src/index.css
git commit -m "feat(studio): dark-only theme (drop light mode + theme toggle)"
```

---

### Task 4: Shared `<PageTitle>` component (display font)

**Files:**
- Create: `dashboard/src/components/page-title.tsx`
- Test: `dashboard/src/components/page-title.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/components/page-title.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { PageTitle } from "./page-title";

describe("PageTitle", () => {
  it("renders the title text in a heading", () => {
    render(<PageTitle>Models</PageTitle>);
    const h = screen.getByRole("heading", { level: 1, name: "Models" });
    expect(h).toBeInTheDocument();
  });

  it("applies the display font utility class", () => {
    render(<PageTitle>Models</PageTitle>);
    const h = screen.getByRole("heading", { level: 1 });
    expect(h.className).toContain("font-display");
  });

  it("renders an optional subtitle", () => {
    render(<PageTitle subtitle="0 models in registry">Models</PageTitle>);
    expect(screen.getByText("0 models in registry")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd dashboard && npx vitest run src/components/page-title.test.tsx`
Expected: FAIL — cannot resolve `./page-title`.

- [ ] **Step 3: Implement the component**

Create `dashboard/src/components/page-title.tsx`:

```tsx
import type { ReactNode } from "react";

interface PageTitleProps {
  children: ReactNode;
  subtitle?: ReactNode;
  className?: string;
}

export function PageTitle({ children, subtitle, className }: PageTitleProps) {
  return (
    <div className={className}>
      <h1 className="font-display text-h1 font-extrabold text-foreground">
        {children}
      </h1>
      {subtitle ? (
        <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd dashboard && npx vitest run src/components/page-title.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/page-title.tsx dashboard/src/components/page-title.test.tsx
git commit -m "feat(studio): shared PageTitle component in display font"
```

---

### Task 5: Adopt `<PageTitle>` on Home and Models

**Files:**
- Modify: `dashboard/src/pages/models.tsx:569` (replace the `<h1>`)
- Modify: `dashboard/src/pages/home.tsx` (replace the page heading)

- [ ] **Step 1: Replace the Models title**

In `dashboard/src/pages/models.tsx`, replace the existing heading at line ~569:

```tsx
<h1 className="text-lg font-semibold tracking-tight">Models</h1>
```

with (importing `PageTitle` at the top — `import { PageTitle } from "@/components/page-title";`):

```tsx
<PageTitle subtitle={`${models.length} models in registry`}>Models</PageTitle>
```

(Use the variable the page already holds for the model count; if the count text is rendered separately below the old `<h1>`, fold it into the `subtitle` and remove the duplicate.)

- [ ] **Step 2: Replace the Home heading**

In `dashboard/src/pages/home.tsx`, import `PageTitle` and replace the top-level page heading element with:

```tsx
<PageTitle subtitle="Overview">Home</PageTitle>
```

(Match the existing heading text; keep surrounding layout containers.)

- [ ] **Step 3: Typecheck + build**

Run: `cd dashboard && npx tsc --noEmit && npm run build`
Expected: both succeed.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/models.tsx dashboard/src/pages/home.tsx
git commit -m "feat(studio): adopt PageTitle on Home and Models"
```

---

### Task 6: E2E — dark-only + display font render correctly

**Files:**
- Create: `dashboard/tests/e2e/brand-foundation.spec.ts`

(Confirm the e2e dir matches the existing Playwright config's `testDir` — adjust the path if the repo uses e.g. `dashboard/e2e/`. Run `cat dashboard/playwright.config.ts` first.)

- [ ] **Step 1: Write the E2E test**

Create `dashboard/tests/e2e/brand-foundation.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

test("app is dark-only", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveClass(/dark/);
  // Background resolves to the brand near-black (#09090b ≈ rgb(9,9,11)).
  const bg = await page.evaluate(() =>
    getComputedStyle(document.body).backgroundColor
  );
  expect(bg).toBe("rgb(9, 9, 11)");
});

test("no theme toggle is present", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /theme/i })).toHaveCount(0);
});

test("page titles use the Bricolage display font", async ({ page }) => {
  await page.goto("/models");
  const h1 = page.getByRole("heading", { level: 1 }).first();
  const family = await h1.evaluate((el) => getComputedStyle(el).fontFamily);
  expect(family.toLowerCase()).toContain("bricolage");
});
```

- [ ] **Step 2: Run the E2E suite**

Run: `cd dashboard && npm run test:e2e -- brand-foundation`
Expected: 3 passing. (If auth gates `/` and `/models`, reuse the existing Playwright auth/storage-state setup the other specs use — check a neighboring spec for the login fixture and apply the same.)

- [ ] **Step 3: Manual browser verification (required for UI)**

Run: `cd dashboard && npm run dev`, open `http://localhost:3001`, and confirm: app is dark, no theme toggle in the shell, "Models" and "Home" titles render in Bricolage (rounder, tighter than Geist), nothing looks broken in the sidebar/header.

- [ ] **Step 4: Commit**

```bash
git add dashboard/tests/e2e/brand-foundation.spec.ts
git commit -m "test(studio): e2e for dark-only + display-font titles"
```

---

## Self-Review

**Spec coverage (§H Branding):** palette unchanged ✓ (no task touches color values); Bricolage added (Tasks 1-2) ✓; type scale added (Task 2) ✓; applied to titles (Tasks 4-5) ✓; dark-only / light theme + toggle removed (Task 3) ✓. No other §H requirement outstanding.

**Placeholder scan:** Tasks 5 and 6 contain *conditional* instructions ("if the count text is rendered separately…", "if auth gates…") rather than placeholders — they hand the engineer the exact edit plus the one branch they must resolve by reading the adjacent code. The Playwright `testDir` is flagged to verify against `playwright.config.ts` before writing the spec. No "TODO/TBD/handle edge cases" left.

**Type consistency:** `PageTitle` props (`children`, `subtitle`, `className`) are defined in Task 4 and used identically in Task 5. `useTheme` still returns `{ theme, resolved, setTheme }` (Task 3) so existing callers don't break.
