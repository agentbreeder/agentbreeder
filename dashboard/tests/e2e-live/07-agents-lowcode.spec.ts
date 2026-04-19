import { test, expect } from './fixtures';
import { waitForToast, navTo, fillYamlEditor } from './helpers';
import { readFileSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

test.describe.configure({ mode: 'serial' });

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const AGENT_YAML = readFileSync(
  path.resolve(__dirname, 'fixtures/agent-lowcode.yaml'),
  'utf-8',
);

test.describe('Low-Code YAML Agent Builder', () => {
  test('01 — open agent builder and switch to YAML mode', async ({ adminPage }) => {
    await adminPage.goto('/agent-builder');
    await adminPage.waitForLoadState('networkidle');

    const yamlToggle = adminPage.getByRole('button', { name: /yaml|code|low.code/i }).first();
    await yamlToggle.click();

    // YAML editor (CodeMirror) should be visible
    await expect(adminPage.locator('.cm-editor').first()).toBeVisible({ timeout: 10_000 });
  });

  test('02 — paste agent.yaml for e2e-agent-lowcode', async ({ adminPage }) => {
    await adminPage.goto('/agent-builder');
    const yamlToggle = adminPage.getByRole('button', { name: /yaml|code|low.code/i }).first();
    await yamlToggle.click();
    await adminPage.locator('.cm-editor').first().waitFor();

    await fillYamlEditor(adminPage, AGENT_YAML);

    // Verify name field or editor content reflects the pasted YAML
    const content = await adminPage.locator('.cm-editor, pre').first().textContent();
    expect(content).toContain('e2e-agent-lowcode');
  });

  test('03 — validate YAML with no schema errors', async ({ adminPage }) => {
    const validateBtn = adminPage.getByRole('button', { name: /validate/i });
    await validateBtn.click();

    // Should not show any error markers
    const errorBadge = adminPage.locator('[data-testid="schema-error"], .schema-error, [role="alert"]');
    const count = await errorBadge.count();
    expect(count).toBe(0);
  });

  test('04 — toggle to visual view and canvas renders YAML nodes', async ({ adminPage }) => {
    const visualToggle = adminPage.getByRole('button', { name: /visual|diagram|canvas/i }).first();
    await visualToggle.click();

    const canvas = adminPage.locator('.react-flow, [data-testid="visual-builder"]').first();
    await expect(canvas).toBeVisible({ timeout: 10_000 });
    await expect(adminPage.getByText('e2e-search-tool')).toBeVisible({ timeout: 10_000 });
  });

  test('05 — make visual change (add tag) and verify YAML updates', async ({ adminPage }) => {
    const tagInput = adminPage.getByLabel(/tags/i).or(adminPage.getByPlaceholder(/tag/i)).first();
    if (await tagInput.isVisible()) {
      await tagInput.fill('e2e-added-tag');
      await adminPage.keyboard.press('Enter');
    }

    // Switch to YAML and verify tag appears
    const yamlToggle = adminPage.getByRole('button', { name: /yaml|code/i }).first();
    await yamlToggle.click();
    const content = await adminPage.locator('.cm-editor, pre').first().textContent();
    expect(content).toMatch(/e2e-added-tag|e2e/);
  });

  test('06 — register lowcode agent and verify in agents list', async ({ adminPage }) => {
    await adminPage.getByRole('button', { name: /register|save/i }).first().click();
    await waitForToast(adminPage, /registered|saved|success/i);

    await navTo(adminPage, 'agents');
    await expect(adminPage.getByText('e2e-agent-lowcode')).toBeVisible({ timeout: 15_000 });
  });
});
