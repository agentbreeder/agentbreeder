import { test, expect } from './fixtures';
import { openSandbox } from './helpers';

test.describe.configure({ mode: 'serial' });

test.describe('Agent Execution via Playground', () => {
  test.beforeAll(async ({ api }) => {
    // Verify both agents exist
    const agents = await api('admin').get('/api/v1/agents?search=e2e-agent') as Array<{ name: string }>;
    const names = Array.isArray(agents) ? agents.map(a => a.name) : [];
    if (!names.includes('e2e-agent-nocode')) {
      throw new Error('e2e-agent-nocode not found — run 06-agents-nocode spec first');
    }
    if (!names.includes('e2e-agent-lowcode')) {
      throw new Error('e2e-agent-lowcode not found — run 07-agents-lowcode spec first');
    }
  });

  test('01 — open playground and select e2e-agent-nocode', async ({ adminPage }) => {
    await openSandbox(adminPage, 'e2e-agent-nocode');
    await expect(adminPage.getByText('e2e-agent-nocode')).toBeVisible({ timeout: 10_000 });
  });

  test('02 — send Hello message and get assistant response', async ({ adminPage }) => {
    await openSandbox(adminPage, 'e2e-agent-nocode');

    const input = adminPage.getByRole('textbox', { name: /message|chat/i }).or(
      adminPage.getByPlaceholder(/type a message|send/i)
    ).first();
    await input.fill('Hello');
    await adminPage.keyboard.press('Enter');

    // Wait for assistant response
    const response = adminPage.locator('[data-role="assistant"], .assistant-message, [data-testid="assistant-msg"]').first();
    await expect(response).toBeVisible({ timeout: 30_000 });
  });

  test('03 — token count badge visible on assistant message', async ({ adminPage }) => {
    const tokenBadge = adminPage.locator(
      '[data-testid="token-count"], .token-count, [aria-label*="token"]'
    ).or(adminPage.getByText(/\d+ tokens?/i)).first();
    await expect(tokenBadge).toBeVisible({ timeout: 10_000 });
  });

  test('04 — send follow-up and conversation history maintained', async ({ adminPage }) => {
    const input = adminPage.getByRole('textbox', { name: /message|chat/i }).or(
      adminPage.getByPlaceholder(/type a message|send/i)
    ).first();
    await input.fill('What can you help me with?');
    await adminPage.keyboard.press('Enter');

    // Should now have 2 user messages + 2 assistant responses
    const userMsgs = adminPage.locator('[data-role="user"], .user-message, [data-testid="user-msg"]');
    await expect(userMsgs).toHaveCount(2, { timeout: 30_000 });
  });

  test('05 — switch to e2e-agent-lowcode and conversation resets', async ({ adminPage }) => {
    await openSandbox(adminPage, 'e2e-agent-lowcode');

    // Previous conversation messages should be gone
    const userMsgs = adminPage.locator('[data-role="user"], .user-message, [data-testid="user-msg"]');
    await expect(userMsgs).toHaveCount(0, { timeout: 5_000 });
  });

  test('06 — override model in playground and send message', async ({ adminPage }) => {
    await openSandbox(adminPage, 'e2e-agent-lowcode');

    const modelOverride = adminPage.getByRole('combobox', { name: /model override|model/i });
    if (await modelOverride.isVisible()) {
      await modelOverride.click();
      await adminPage.getByRole('option').first().click();
    }

    const input = adminPage.getByRole('textbox', { name: /message|chat/i }).or(
      adminPage.getByPlaceholder(/type a message|send/i)
    ).first();
    await input.fill('Testing model override');
    await adminPage.keyboard.press('Enter');

    const response = adminPage.locator('[data-role="assistant"], .assistant-message').first();
    await expect(response).toBeVisible({ timeout: 30_000 });
  });
});
