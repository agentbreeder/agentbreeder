import { test, expect } from './fixtures';
import { waitForToast, navTo } from './helpers';

test.describe.configure({ mode: 'serial' });

test.describe('MCP Server Registration', () => {
  test('01 — register e2e-mcp-fetch server', async ({ adminPage }) => {
    await adminPage.goto('/mcp-servers');
    await adminPage.getByRole('button', { name: /register|add|new/i }).click();
    await adminPage.getByRole('dialog').waitFor();

    await adminPage.getByLabel(/name/i).fill('e2e-mcp-fetch');
    await adminPage.getByLabel(/command/i).fill('npx');
    const argsField = adminPage.getByLabel(/args|arguments/i);
    if (await argsField.isVisible()) {
      await argsField.fill('-y @modelcontextprotocol/server-fetch');
    }
    const teamSelect = adminPage.getByRole('combobox', { name: /team/i });
    if (await teamSelect.isVisible()) {
      await teamSelect.click();
      await adminPage.getByRole('option', { name: 'e2e-team-alpha' }).click();
    }
    await adminPage.getByRole('button', { name: /save|register/i }).click();
    await waitForToast(adminPage, /registered|saved|success/i);
  });

  test('02 — e2e-mcp-fetch appears in MCP server list', async ({ adminPage }) => {
    await navTo(adminPage, 'mcp');
    await expect(adminPage.getByText('e2e-mcp-fetch')).toBeVisible({ timeout: 10_000 });
  });

  test('03 — MCP server detail shows tools list', async ({ adminPage }) => {
    await adminPage.goto('/mcp-servers');
    await adminPage.getByText('e2e-mcp-fetch').click();
    await adminPage.waitForURL(/mcp-server/);
    const toolsList = adminPage.locator('[data-testid="tools-list"], .tools-list, [aria-label="Tools"]');
    await expect(toolsList.or(adminPage.getByText(/tools/i)).first()).toBeVisible({ timeout: 15_000 });
  });

  test('04 — register e2e-mcp-memory server', async ({ adminPage }) => {
    await adminPage.goto('/mcp-servers');
    await adminPage.getByRole('button', { name: /register|add|new/i }).click();
    await adminPage.getByRole('dialog').waitFor();

    await adminPage.getByLabel(/name/i).fill('e2e-mcp-memory');
    await adminPage.getByLabel(/command/i).fill('npx');
    const argsField = adminPage.getByLabel(/args|arguments/i);
    if (await argsField.isVisible()) {
      await argsField.fill('-y @modelcontextprotocol/server-memory');
    }
    await adminPage.getByRole('button', { name: /save|register/i }).click();
    await waitForToast(adminPage, /registered|saved|success/i);
  });

  test('05 — deregister e2e-mcp-fetch and verify removed', async ({ adminPage }) => {
    await adminPage.goto('/mcp-servers');
    const row = adminPage.getByRole('row').filter({ hasText: 'e2e-mcp-fetch' });
    await row.getByRole('button', { name: /delete|remove|deregister/i }).click();
    const dialog = adminPage.getByRole('dialog');
    if (await dialog.isVisible()) {
      await dialog.getByRole('button', { name: /confirm|yes|delete/i }).click();
    }
    await waitForToast(adminPage, /deleted|removed/i);
    await expect(adminPage.getByText('e2e-mcp-fetch')).not.toBeVisible();
  });

  test('06 — e2e-mcp-memory still present after other deregister', async ({ adminPage }) => {
    await adminPage.goto('/mcp-servers');
    await expect(adminPage.getByText('e2e-mcp-memory')).toBeVisible({ timeout: 10_000 });
  });

  test('07 — e2e-mcp-memory selectable in agent builder MCP picker', async ({ adminPage }) => {
    await adminPage.goto('/agent-builder');
    await adminPage.waitForLoadState('networkidle');
    const addMcpBtn = adminPage.getByRole('button', { name: /add mcp|mcp server/i }).first();
    await addMcpBtn.click();
    await adminPage.getByRole('dialog').waitFor();
    await expect(adminPage.getByText('e2e-mcp-memory')).toBeVisible({ timeout: 10_000 });
    await adminPage.keyboard.press('Escape');
  });
});
