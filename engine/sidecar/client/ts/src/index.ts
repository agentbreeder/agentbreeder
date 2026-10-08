export interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export class APSClient {
  private readonly url: string
  private readonly apiKey: string

  constructor(opts?: { url?: string; apiKey?: string }) {
    this.url = opts?.url ?? process.env.AGENTBREEDER_URL ?? 'http://agentbreeder-api:8000'
    this.apiKey = opts?.apiKey ?? process.env.AGENTBREEDER_API_KEY ?? ''
  }

  // Memory
  memory = {
    load: (threadId: string): Promise<Message[]> =>
      this._get<Message[]>(`/api/v1/memory/thread/${threadId}`),
    save: (threadId: string, messages: Message[]): Promise<void> =>
      this._post<void>('/api/v1/memory/thread', { thread_id: threadId, messages }),
  }

  // Tool execution
  tools = {
    execute: (name: string, input: unknown): Promise<unknown> =>
      this._post<unknown>('/api/v1/tools/sandbox/execute', { name, input }),
  }

  private async _get<T>(path: string): Promise<T> {
    return this._request<T>('GET', path)
  }

  private async _post<T>(path: string, body: unknown): Promise<T> {
    return this._request<T>('POST', path, body)
  }

  private async _request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${this.url}${path}`
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${this.apiKey}`,
    }
    const init: RequestInit = { method, headers }
    if (body !== undefined) {
      init.body = JSON.stringify(body)
    }

    let lastError: unknown
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const res = await fetch(url, init)
        if (res.ok) {
          // For void responses (204 No Content)
          if (res.status === 204) return undefined as T
          return res.json() as Promise<T>
        }
        if (res.status >= 500) {
          lastError = new Error(`HTTP ${res.status}: ${await res.text()}`)
          if (attempt < 2) {
            await new Promise(r => setTimeout(r, 500 * 2 ** attempt))
            continue
          }
        } else {
          throw new Error(`HTTP ${res.status}: ${await res.text()}`)
        }
      } catch (err) {
        lastError = err
        if (attempt < 2) {
          await new Promise(r => setTimeout(r, 500 * 2 ** attempt))
          continue
        }
      }
    }
    throw lastError
  }
}
