import { test, expect } from './fixtures';
import { waitForToast, pollUntil } from './helpers';
import path from 'path';
import { fileURLToPath } from 'url';

test.describe.configure({ mode: 'serial' });

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_DOC = path.resolve(__dirname, 'fixtures/sample-doc.txt');

test.describe('RAG Index Lifecycle', () => {
  test('01 — create e2e-kb-docs RAG index with pgvector', async ({ adminPage }) => {
    await adminPage.goto('/rag-builder');
    await adminPage.waitForLoadState('networkidle');

    await adminPage.getByLabel(/name/i).fill('e2e-kb-docs');
    await adminPage.getByLabel(/description/i).fill('E2E test knowledge base').catch(() => {});

    const backendSelect = adminPage.getByRole('combobox', { name: /backend|vector store|type/i });
    if (await backendSelect.isVisible()) {
      await backendSelect.click();
      await adminPage.getByRole('option', { name: /pgvector|postgres/i }).click();
    }

    const teamSelect = adminPage.getByRole('combobox', { name: /team/i });
    if (await teamSelect.isVisible()) {
      await teamSelect.click();
      await adminPage.getByRole('option', { name: 'e2e-team-alpha' }).click();
    }

    await adminPage.getByRole('button', { name: /save|create/i }).click();
    await waitForToast(adminPage, /saved|created|success/i);
  });

  test('02 — RAG index appears in list', async ({ adminPage }) => {
    await adminPage.goto('/rag');
    await expect(adminPage.getByText('e2e-kb-docs')).toBeVisible({ timeout: 10_000 });
  });

  test('03 — upload a sample document to the index', async ({ adminPage }) => {
    await adminPage.goto('/rag');
    await adminPage.getByText('e2e-kb-docs').click();
    await adminPage.waitForURL(/rag\//);

    const fileInput = adminPage.locator('input[type="file"]');
    await fileInput.setInputFiles(SAMPLE_DOC);

    const uploadBtn = adminPage.getByRole('button', { name: /upload|ingest/i });
    if (await uploadBtn.isVisible()) await uploadBtn.click();

    await waitForToast(adminPage, /upload|ingesting|processing/i);
  });

  test('04 — wait for ingestion status to become ready', async ({ adminPage, api }) => {
    await pollUntil(async () => {
      const data = await api('admin').get('/api/v1/rag?search=e2e-kb-docs') as Array<{ name: string; status: string }>;
      const kb = Array.isArray(data) ? data.find((d) => d.name === 'e2e-kb-docs') : null;
      return kb?.status === 'ready' || kb?.status === 'indexed';
    }, 60_000, 3_000);

    await adminPage.reload();
    await expect(adminPage.getByText(/ready|indexed/i).first()).toBeVisible({ timeout: 15_000 });
  });

  test('05 — run test search and get at least one chunk', async ({ adminPage }) => {
    await adminPage.goto('/rag');
    await adminPage.getByText('e2e-kb-docs').click();

    const searchInput = adminPage.getByPlaceholder(/search|query/i).or(
      adminPage.getByRole('searchbox')
    ).first();
    await searchInput.fill('hello');
    await adminPage.getByRole('button', { name: /search|test/i }).click();

    const results = adminPage.locator('[data-testid="search-results"], .search-results, .chunk');
    await expect(results.first()).toBeVisible({ timeout: 15_000 });
  });

  test('06 — RAG index selectable in agent builder knowledge base picker', async ({ adminPage }) => {
    await adminPage.goto('/agent-builder');
    await adminPage.waitForLoadState('networkidle');
    const addKbBtn = adminPage.getByRole('button', { name: /add knowledge|knowledge base|rag/i }).first();
    await addKbBtn.click();
    await adminPage.getByRole('dialog').waitFor();
    await expect(adminPage.getByText('e2e-kb-docs')).toBeVisible({ timeout: 10_000 });
    await adminPage.keyboard.press('Escape');
  });
});
