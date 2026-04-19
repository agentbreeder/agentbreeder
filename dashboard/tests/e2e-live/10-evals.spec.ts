import { test, expect } from './fixtures';
import { waitForToast, pollUntil } from './helpers';

test.describe.configure({ mode: 'serial' });

test.describe('Agent Evaluation', () => {
  test('01 — create e2e-eval-dataset', async ({ adminPage }) => {
    await adminPage.goto('/eval-datasets');
    await adminPage.getByRole('button', { name: /new dataset|create/i }).click();
    await adminPage.getByRole('dialog').or(adminPage.waitForURL(/eval-dataset/)).then(() => {});

    await adminPage.getByLabel(/name/i).fill('e2e-eval-dataset');
    await adminPage.getByRole('button', { name: /save|create/i }).click();
    await waitForToast(adminPage, /created|saved/i);
  });

  test('02 — add 3 test cases to dataset', async ({ adminPage }) => {
    await adminPage.goto('/eval-datasets');
    await adminPage.getByText('e2e-eval-dataset').click();
    await adminPage.waitForURL(/eval-dataset/);

    const cases = [
      { input: 'What is AgentBreeder?', expected: 'AgentBreeder' },
      { input: 'How do I deploy an agent?', expected: 'deploy' },
      { input: 'What is RBAC?', expected: 'access control' },
    ];

    for (const tc of cases) {
      await adminPage.getByRole('button', { name: /add case|new case|add test/i }).click();
      await adminPage.getByLabel(/input|question/i).last().fill(tc.input);
      await adminPage.getByLabel(/expected|output/i).last().fill(tc.expected);
      await adminPage.getByRole('button', { name: /save case|add/i }).click().catch(() => {});
    }

    await adminPage.getByRole('button', { name: /save|done/i }).click().catch(() => {});
  });

  test('03 — dataset shows 3 test cases count', async ({ adminPage }) => {
    await adminPage.goto('/eval-datasets');
    const datasetRow = adminPage.getByRole('row').filter({ hasText: 'e2e-eval-dataset' });
    await expect(datasetRow.getByText(/3/)).toBeVisible({ timeout: 10_000 });
  });

  test('04 — create eval run for e2e-agent-nocode', async ({ adminPage }) => {
    await adminPage.goto('/eval-runs');
    await adminPage.getByRole('button', { name: /new run|create run|run eval/i }).click();
    await adminPage.getByRole('dialog').waitFor();

    const agentSelect = adminPage.getByRole('combobox', { name: /agent/i });
    await agentSelect.click();
    await adminPage.getByRole('option', { name: 'e2e-agent-nocode' }).click();

    const datasetSelect = adminPage.getByRole('combobox', { name: /dataset/i });
    await datasetSelect.click();
    await adminPage.getByRole('option', { name: 'e2e-eval-dataset' }).click();

    await adminPage.getByRole('button', { name: /start|run|create/i }).click();
    await waitForToast(adminPage, /started|running|created/i);
  });

  test('05 — wait for eval run to complete', async ({ adminPage, api }) => {
    await pollUntil(async () => {
      const runs = await api('admin').get('/api/v1/evals/runs?search=e2e-agent-nocode') as Array<{ status: string }>;
      return Array.isArray(runs) && runs.some(r => r.status === 'completed' || r.status === 'done');
    }, 120_000, 5_000);

    await adminPage.goto('/eval-runs');
    await adminPage.reload();
    await expect(adminPage.getByText(/completed|done/i).first()).toBeVisible({ timeout: 15_000 });
  });

  test('06 — open run detail and see per-case scores', async ({ adminPage }) => {
    await adminPage.goto('/eval-runs');
    await adminPage.getByRole('row').nth(1).click();
    await adminPage.waitForURL(/eval-run/);

    // Should show score per test case
    const scores = adminPage.locator('[data-testid="eval-score"], .eval-score').or(
      adminPage.getByText(/pass|fail|\d+%|\d+\.\d+/i).first()
    );
    await expect(scores).toBeVisible({ timeout: 10_000 });
  });

  test('07 — create second eval run with e2e-agent-lowcode', async ({ adminPage }) => {
    await adminPage.goto('/eval-runs');
    await adminPage.getByRole('button', { name: /new run|create run|run eval/i }).click();
    await adminPage.getByRole('dialog').waitFor();

    const agentSelect = adminPage.getByRole('combobox', { name: /agent/i });
    await agentSelect.click();
    await adminPage.getByRole('option', { name: 'e2e-agent-lowcode' }).click();

    const datasetSelect = adminPage.getByRole('combobox', { name: /dataset/i });
    await datasetSelect.click();
    await adminPage.getByRole('option', { name: 'e2e-eval-dataset' }).click();

    await adminPage.getByRole('button', { name: /start|run|create/i }).click();
    await waitForToast(adminPage, /started|running|created/i);
  });

  test('08 — compare two eval runs side-by-side', async ({ adminPage }) => {
    await adminPage.goto('/eval-runs');

    // Select both runs for comparison
    const checkboxes = adminPage.getByRole('checkbox');
    await checkboxes.nth(0).check();
    await checkboxes.nth(1).check();

    const compareBtn = adminPage.getByRole('button', { name: /compare/i });
    await compareBtn.click();
    await adminPage.waitForURL(/eval-comparison|compare/);

    const table = adminPage.getByRole('table').or(
      adminPage.locator('[data-testid="comparison-table"]')
    );
    await expect(table).toBeVisible({ timeout: 10_000 });
  });
});
