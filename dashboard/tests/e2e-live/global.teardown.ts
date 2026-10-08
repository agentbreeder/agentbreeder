import { test as teardown } from '@playwright/test';
import { existsSync, readFileSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const API = process.env.E2E_API_BASE_URL ?? 'http://localhost:8000';

async function deleteByNamePrefix(
  endpoint: string,
  token: string,
  prefix: string,
): Promise<void> {
  const res = await fetch(`${API}${endpoint}?search=${prefix}&page=1&page_size=100`, {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  if (!res.ok) return;
  const body = await res.json();
  const items: Array<{ id: string; name: string }> = body.data ?? body ?? [];
  const toDelete = items.filter((i) => i.name?.startsWith(prefix));
  for (const item of toDelete) {
    await fetch(`${API}${endpoint}/${item.id}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` },
    });
    console.log(`Deleted ${endpoint}/${item.id} (${item.name})`);
  }
}

teardown('clean up all e2e-* test data', async () => {
  const statePath = path.resolve(__dirname, '../../../.e2e-state.json');
  if (!existsSync(statePath)) {
    console.log('No .e2e-state.json found — skipping teardown');
    return;
  }
  const state = JSON.parse(readFileSync(statePath, 'utf-8'));
  const token: string = state.adminToken;
  if (!token || typeof token !== 'string' || !token.trim()) {
    throw new Error('Invalid or missing adminToken in .e2e-state.json — cannot clean up');
  }

  const endpoints = [
    '/api/v1/agents',
    '/api/v1/prompts',
    '/api/v1/tools',
    '/api/v1/mcp_servers',
    '/api/v1/providers',
  ];

  for (const ep of endpoints) {
    await deleteByNamePrefix(ep, token, 'e2e-');
  }

  // Delete teams
  for (const teamId of [state.teamAlphaId, state.teamBetaId]) {
    if (teamId && teamId !== 'unknown') {
      await fetch(`${API}/api/v1/teams/${teamId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` },
      });
      console.log(`Deleted team ${teamId}`);
    }
  }

  console.log('Teardown complete');
});
