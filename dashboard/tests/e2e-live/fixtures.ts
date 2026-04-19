import { test as base, type Page, type BrowserContext } from '@playwright/test';
import { readFileSync, existsSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

export { expect } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const API = process.env.E2E_API_BASE_URL ?? 'http://localhost:8000';

function readState() {
  const p = path.resolve(__dirname, '../../../.e2e-state.json');
  if (!existsSync(p)) throw new Error('.e2e-state.json not found — run setup first');
  return JSON.parse(readFileSync(p, 'utf-8')) as {
    adminToken: string;
    memberToken: string;
    viewerToken: string;
    teamAlphaId: string;
    teamBetaId: string;
    litellmProviderId: string;
  };
}

type Role = 'admin' | 'member' | 'viewer';

interface ApiClient {
  get(path: string): Promise<unknown>;
  post(path: string, body: unknown): Promise<unknown>;
  delete(path: string): Promise<void>;
}

function makeApiClient(token: string): ApiClient {
  const headers = () => ({
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  });
  return {
    async get(path: string) {
      const res = await fetch(`${API}${path}`, { headers: headers() });
      if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
      const body = await res.json();
      return body.data ?? body;
    },
    async post(path: string, body: unknown) {
      const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`POST ${path}: ${res.status} ${await res.text()}`);
      const json = await res.json();
      return json.data ?? json;
    },
    async delete(path: string) {
      await fetch(`${API}${path}`, { method: 'DELETE', headers: headers() });
    },
  };
}

interface LiveFixtures {
  adminPage: Page;
  memberPage: Page;
  viewerPage: Page;
  api: (role: Role) => ApiClient;
  state: ReturnType<typeof readState>;
}

export const test = base.extend<LiveFixtures>({
  adminPage: async ({ browser }, use) => {
    const ctx: BrowserContext = await browser.newContext({
      storageState: path.resolve(__dirname, '../../../.auth/admin.json'),
    });
    const page = await ctx.newPage();
    await use(page);
    await ctx.close();
  },

  memberPage: async ({ browser }, use) => {
    const ctx: BrowserContext = await browser.newContext({
      storageState: path.resolve(__dirname, '../../../.auth/member.json'),
    });
    const page = await ctx.newPage();
    await use(page);
    await ctx.close();
  },

  viewerPage: async ({ browser }, use) => {
    const ctx: BrowserContext = await browser.newContext({
      storageState: path.resolve(__dirname, '../../../.auth/viewer.json'),
    });
    const page = await ctx.newPage();
    await use(page);
    await ctx.close();
  },

  api: async (_fixtures, use) => {
    const state = readState();
    const clients: Record<Role, ApiClient> = {
      admin: makeApiClient(state.adminToken),
      member: makeApiClient(state.memberToken),
      viewer: makeApiClient(state.viewerToken),
    };
    await use((role: Role) => clients[role]);
  },

  state: async (_fixtures, use) => {
    await use(readState());
  },
});
