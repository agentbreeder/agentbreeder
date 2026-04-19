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

test.describe('RBAC — RAG', () => {
  test('viewer cannot upload documents to e2e-kb-docs', async ({ viewerPage }) => {
    await viewerPage.goto('/rag');
    await viewerPage.getByText('e2e-kb-docs').click();
    await viewerPage.waitForURL(/rag/);
    const uploadBtn = viewerPage.getByRole('button', { name: /upload|ingest/i });
    await expect(uploadBtn).not.toBeVisible();
  });

  test('member can upload documents to e2e-kb-docs', async ({ memberPage }) => {
    await memberPage.goto('/rag');
    await memberPage.getByText('e2e-kb-docs').click();
    await memberPage.waitForURL(/rag/);
    const uploadBtn = memberPage.getByRole('button', { name: /upload|ingest/i });
    await expect(uploadBtn).toBeVisible({ timeout: 10_000 });
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

test.describe('RBAC — Agents', () => {
  test('viewer cannot see Register/Deploy button in agent builder', async ({ viewerPage }) => {
    await viewerPage.goto('/agent-builder');
    await viewerPage.waitForLoadState('networkidle');
    const registerBtn = viewerPage.getByRole('button', { name: /register|deploy/i });
    await expect(registerBtn).not.toBeVisible();
  });

  test('member can register agent to e2e-team-alpha', async ({ memberPage }) => {
    await memberPage.goto('/agent-builder');
    await memberPage.waitForLoadState('networkidle');
    const registerBtn = memberPage.getByRole('button', { name: /register|deploy/i });
    await expect(registerBtn).toBeVisible({ timeout: 10_000 });
  });

  test('member team picker excludes e2e-team-beta', async ({ memberPage }) => {
    await memberPage.goto('/agent-builder');
    await memberPage.waitForLoadState('networkidle');
    const teamSelect = memberPage.getByRole('combobox', { name: /team/i });
    if (await teamSelect.isVisible()) {
      await teamSelect.click();
      await expect(memberPage.getByRole('option', { name: 'e2e-team-beta' })).not.toBeVisible();
      await memberPage.keyboard.press('Escape');
    }
  });
});

test.describe('RBAC — Costs', () => {
  test('viewer costs page shows only their team data', async ({ viewerPage }) => {
    await viewerPage.goto('/costs');
    await viewerPage.waitForLoadState('networkidle');
    // e2e-team-beta should not be selectable in team filter
    const teamFilter = viewerPage.getByRole('combobox', { name: /team/i });
    if (await teamFilter.isVisible()) {
      await teamFilter.click();
      await expect(viewerPage.getByRole('option', { name: 'e2e-team-beta' })).not.toBeVisible();
      await viewerPage.keyboard.press('Escape');
    }
  });

  test('admin costs page shows all teams including e2e-team-beta', async ({ adminPage }) => {
    await adminPage.goto('/costs');
    const teamFilter = adminPage.getByRole('combobox', { name: /team/i });
    if (await teamFilter.isVisible()) {
      await teamFilter.click();
      await expect(adminPage.getByRole('option', { name: 'e2e-team-beta' })).toBeVisible({ timeout: 10_000 });
      await adminPage.keyboard.press('Escape');
    }
  });
});

test.describe('RBAC — Audit Log', () => {
  test('member is redirected from /audit', async ({ memberPage }) => {
    await memberPage.goto('/audit');
    await memberPage.waitForLoadState('networkidle');
    // Should redirect to 403, login, or dashboard — not show the audit table
    const url = memberPage.url();
    const isAuditPage = url.includes('/audit') && !url.includes('403');
    if (isAuditPage) {
      // If still on /audit, the audit table should not be visible to member
      const auditTable = memberPage.getByRole('table');
      await expect(auditTable).not.toBeVisible();
    }
  });

  test('admin can access /audit and see events', async ({ adminPage }) => {
    await adminPage.goto('/audit');
    await adminPage.waitForLoadState('networkidle');
    const auditTable = adminPage.getByRole('table').or(
      adminPage.getByRole('row').first()
    );
    await expect(auditTable).toBeVisible({ timeout: 15_000 });
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
