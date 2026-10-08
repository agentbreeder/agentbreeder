import { test, expect } from './fixtures';

test.describe.configure({ mode: 'serial' });

test.describe('RBAC — Prompts', () => {
  test('viewer cannot see Edit button on e2e-support-prompt', async ({ viewerPage }) => {
    await viewerPage.goto('/prompts');
    await viewerPage.getByText('e2e-support-prompt').click();
    await viewerPage.waitForURL(/prompt/);
    const editBtn = viewerPage.getByRole('button', { name: /edit/i });
    await expect(editBtn).not.toBeVisible();
  });

  test('member can edit e2e-support-prompt (same team)', async ({ memberPage }) => {
    await memberPage.goto('/prompts');
    await memberPage.getByText('e2e-support-prompt').click();
    await memberPage.waitForURL(/prompt/);
    const editBtn = memberPage.getByRole('button', { name: /edit/i });
    await expect(editBtn).toBeVisible({ timeout: 10_000 });
  });

  test('member cannot see prompts owned by e2e-team-beta', async ({ memberPage }) => {
    // e2e-team-beta prompts should be absent from member's view
    await memberPage.goto('/prompts');
    const betaItems = memberPage.getByRole('row').filter({ hasText: 'e2e-team-beta' });
    await expect(betaItems).toHaveCount(0);
  });
});

test.describe('RBAC — Tools', () => {
  test('viewer cannot open sandbox runner', async ({ viewerPage }) => {
    await viewerPage.goto('/tools');
    await viewerPage.getByText('e2e-search-tool').click();
    await viewerPage.waitForURL(/tool/);
    const sandboxBtn = viewerPage.getByRole('button', { name: /sandbox|run|execute/i });
    await expect(sandboxBtn).not.toBeVisible();
  });

  test('member can open and execute sandbox runner', async ({ memberPage }) => {
    await memberPage.goto('/tools');
    await memberPage.getByText('e2e-search-tool').click();
    await memberPage.waitForURL(/tool/);
    const sandboxBtn = memberPage.getByRole('button', { name: /sandbox|run|execute/i });
    await expect(sandboxBtn).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('RBAC — MCP Servers', () => {
  test('viewer sees MCP detail as read-only (no deregister)', async ({ viewerPage }) => {
    await viewerPage.goto('/mcp-servers');
    await viewerPage.getByText('e2e-mcp-memory').click();
    await viewerPage.waitForURL(/mcp-server/);
    const deregBtn = viewerPage.getByRole('button', { name: /deregister|delete|remove/i });
    await expect(deregBtn).not.toBeVisible();
  });

  test('admin sees deregister button on MCP detail', async ({ adminPage }) => {
    await adminPage.goto('/mcp-servers');
    await adminPage.getByText('e2e-mcp-memory').click();
    await adminPage.waitForURL(/mcp-server/);
    const deregBtn = adminPage.getByRole('button', { name: /deregister|delete|remove/i });
    await expect(deregBtn).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('RBAC — Teams', () => {
  test('member does not see Create Team button', async ({ memberPage }) => {
    await memberPage.goto('/teams');
    await memberPage.waitForLoadState('networkidle');
    const createBtn = memberPage.getByRole('button', { name: /create team|new team/i });
    await expect(createBtn).not.toBeVisible();
  });

  test('admin sees Create Team button and dialog opens', async ({ adminPage }) => {
    await adminPage.goto('/teams');
    await adminPage.waitForLoadState('networkidle');
    const createBtn = adminPage.getByRole('button', { name: /create team|new team/i });
    await expect(createBtn).toBeVisible({ timeout: 10_000 });
    await createBtn.click();
    await expect(adminPage.getByRole('dialog')).toBeVisible();
    await adminPage.keyboard.press('Escape');
  });
});
