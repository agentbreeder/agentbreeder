import { test, expect } from './fixtures';
import { waitForToast, navTo } from './helpers';

test.describe.configure({ mode: 'serial' });

test.describe('No-Code Agent Builder', () => {
  test.beforeAll(async ({ api }) => {
    // Seed dependencies if missing from prior spec runs
    const prompts = await api('admin').get('/api/v1/prompts?search=e2e-support-prompt') as Array<{ name: string }>;
    if (!Array.isArray(prompts) || !prompts.some(p => p.name === 'e2e-support-prompt')) {
      await api('admin').post('/api/v1/prompts', {
        name: 'e2e-support-prompt',
        system: 'You are a helpful e2e support agent.',
        team: 'e2e-team-alpha',
      });
    }
    const tools = await api('admin').get('/api/v1/tools?search=e2e-search-tool') as Array<{ name: string }>;
    if (!Array.isArray(tools) || !tools.some(t => t.name === 'e2e-search-tool')) {
      await api('admin').post('/api/v1/tools', {
        name: 'e2e-search-tool',
        description: 'E2E search tool',
        schema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
        team: 'e2e-team-alpha',
      });
    }
  });

  test('01 — agent builder visual canvas loads', async ({ adminPage }) => {
    await adminPage.goto('/agent-builder');
    await adminPage.waitForLoadState('networkidle');
    // Visual canvas (ReactFlow) should render
    const canvas = adminPage.locator('.react-flow, [data-testid="visual-builder"], canvas').first();
    await expect(canvas).toBeVisible({ timeout: 15_000 });
  });

  test('02 — set agent name, framework, and team', async ({ adminPage }) => {
    await adminPage.goto('/agent-builder');
    await adminPage.waitForLoadState('networkidle');

    await adminPage.getByLabel(/name/i).fill('e2e-agent-nocode');

    const frameworkSelect = adminPage.getByRole('combobox', { name: /framework/i });
    await frameworkSelect.click();
    await adminPage.getByRole('option', { name: /claude.sdk|claude_sdk/i }).click();

    const teamSelect = adminPage.getByRole('combobox', { name: /team/i });
    await teamSelect.click();
    await adminPage.getByRole('option', { name: 'e2e-team-alpha' }).click();
  });

  test('03 — attach prompt from registry picker', async ({ adminPage }) => {
    await adminPage.goto('/agent-builder');
    await adminPage.getByLabel(/name/i).fill('e2e-agent-nocode');

    const addPromptBtn = adminPage.getByRole('button', { name: /add prompt|prompt/i }).first();
    await addPromptBtn.click();
    await adminPage.getByRole('dialog').waitFor();
    await adminPage.getByRole('searchbox').or(adminPage.getByPlaceholder(/search/i)).fill('e2e-support-prompt');
    await adminPage.getByText('e2e-support-prompt').click();
    await adminPage.keyboard.press('Escape').catch(() => {});

    // Verify it appears as attached
    await expect(adminPage.getByText('e2e-support-prompt')).toBeVisible();
  });

  test('04 — attach tool from registry picker', async ({ adminPage }) => {
    // Continue from same page state or re-navigate
    const addToolBtn = adminPage.getByRole('button', { name: /add tool/i }).first();
    await addToolBtn.click();
    await adminPage.getByRole('dialog').waitFor();
    await adminPage.getByRole('searchbox').or(adminPage.getByPlaceholder(/search/i)).fill('e2e-search-tool');
    await adminPage.getByText('e2e-search-tool').click();
    await adminPage.keyboard.press('Escape').catch(() => {});
    await expect(adminPage.getByText('e2e-search-tool')).toBeVisible();
  });

  test('05 — attach RAG knowledge base', async ({ adminPage }) => {
    const addKbBtn = adminPage.getByRole('button', { name: /add knowledge|knowledge base|rag/i }).first();
    await addKbBtn.click();
    await adminPage.getByRole('dialog').waitFor();
    await adminPage.getByRole('searchbox').or(adminPage.getByPlaceholder(/search/i)).fill('e2e-kb-docs');
    await adminPage.getByText('e2e-kb-docs').click();
    await adminPage.keyboard.press('Escape').catch(() => {});
    await expect(adminPage.getByText('e2e-kb-docs')).toBeVisible();
  });

  test('06 — attach MCP server', async ({ adminPage }) => {
    const addMcpBtn = adminPage.getByRole('button', { name: /add mcp|mcp server/i }).first();
    await addMcpBtn.click();
    await adminPage.getByRole('dialog').waitFor();
    await adminPage.getByRole('searchbox').or(adminPage.getByPlaceholder(/search/i)).fill('e2e-mcp-memory');
    await adminPage.getByText('e2e-mcp-memory').click();
    await adminPage.keyboard.press('Escape').catch(() => {});
    await expect(adminPage.getByText('e2e-mcp-memory')).toBeVisible();
  });

  test('07 — toggle View YAML and verify agent.yaml contains all refs', async ({ adminPage }) => {
    const yamlToggle = adminPage.getByRole('button', { name: /view yaml|yaml|code/i }).first();
    await yamlToggle.click();

    const yamlContent = await adminPage.locator('.cm-editor, pre, code, textarea').first().textContent();
    expect(yamlContent).toContain('e2e-agent-nocode');
    expect(yamlContent).toContain('e2e-support-prompt');
    expect(yamlContent).toContain('e2e-search-tool');
    expect(yamlContent).toContain('e2e-kb-docs');
    expect(yamlContent).toContain('e2e-mcp-memory');
  });

  test('08 — toggle back to visual and canvas nodes preserved', async ({ adminPage }) => {
    const visualToggle = adminPage.getByRole('button', { name: /visual|diagram|canvas/i }).first();
    await visualToggle.click();

    // Canvas should still show all attached resources as nodes
    await expect(adminPage.getByText('e2e-support-prompt')).toBeVisible();
    await expect(adminPage.getByText('e2e-search-tool')).toBeVisible();
  });

  test('09 — register agent and verify in agents list', async ({ adminPage }) => {
    await adminPage.getByRole('button', { name: /register|save|deploy/i }).first().click();
    await waitForToast(adminPage, /registered|saved|success/i);

    await navTo(adminPage, 'agents');
    await expect(adminPage.getByText('e2e-agent-nocode')).toBeVisible({ timeout: 15_000 });
  });
});
