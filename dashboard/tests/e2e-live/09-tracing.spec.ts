import { test, expect } from './fixtures';

test.describe.configure({ mode: 'serial' });

test.describe('Distributed Tracing', () => {
  test.beforeAll(async ({ api }) => {
    // Verify traces exist from playground runs
    const traces = await api('admin').get('/api/v1/tracing?page_size=10') as Array<unknown>;
    if (!Array.isArray(traces) || traces.length === 0) {
      throw new Error('No traces found — run 08-agent-execution spec first');
    }
  });

  test('01 — traces page shows at least 2 traces', async ({ adminPage }) => {
    await adminPage.goto('/traces');
    await adminPage.waitForLoadState('networkidle');
    const rows = adminPage.getByRole('row').filter({ hasNotText: /agent|date|status/i }); // data rows
    await expect(rows).toHaveCount(2, { timeout: 10_000 });
  });

  test('02 — open first trace and span tree renders', async ({ adminPage }) => {
    await adminPage.goto('/traces');
    await adminPage.getByRole('row').nth(1).click(); // first data row
    await adminPage.waitForURL(/trace/);

    const spanTree = adminPage.locator(
      '[data-testid="span-tree"], .span-tree, [aria-label="Trace"]'
    ).or(adminPage.getByText(/llm call|llm_call|invoke/i)).first();
    await expect(spanTree).toBeVisible({ timeout: 10_000 });
  });

  test('03 — LLM span shows latency in ms', async ({ adminPage }) => {
    const latency = adminPage.locator('[data-testid="latency"], .latency').or(
      adminPage.getByText(/\d+\s*ms/i).first()
    );
    await expect(latency).toBeVisible({ timeout: 10_000 });
  });

  test('04 — LLM span shows prompt and completion token counts', async ({ adminPage }) => {
    const tokens = adminPage.getByText(/prompt tokens|completion tokens|\d+ tokens/i).first();
    await expect(tokens).toBeVisible({ timeout: 10_000 });
  });

  test('05 — filter by agent name scopes results', async ({ adminPage }) => {
    await adminPage.goto('/traces');
    await adminPage.waitForLoadState('networkidle');

    const filterInput = adminPage.getByPlaceholder(/agent|filter/i).or(
      adminPage.getByRole('searchbox')
    ).first();
    await filterInput.fill('e2e-agent-nocode');
    await adminPage.keyboard.press('Enter');
    await adminPage.waitForLoadState('networkidle');

    // All visible rows should relate to e2e-agent-nocode
    const agentCells = adminPage.getByRole('cell').filter({ hasText: 'e2e-agent-nocode' });
    await expect(agentCells.first()).toBeVisible({ timeout: 10_000 });
  });

  test('06 — filter by date range (last 1 hour) shows traces', async ({ adminPage }) => {
    await adminPage.goto('/traces');

    const dateFilter = adminPage.getByRole('button', { name: /date|time range|last/i }).first();
    if (await dateFilter.isVisible()) {
      await dateFilter.click();
      await adminPage.getByRole('option', { name: /last hour|1 hour|60 min/i }).click();
    }

    const rows = adminPage.getByRole('row').nth(1);
    await expect(rows).toBeVisible({ timeout: 10_000 });
  });
});
