// The API client. Every reply is an envelope `{data, error: null | {code, message}, stale}`.
// The session is a header-bearer token (never a cookie, console spec §8.1 #1); requests omit
// credentials and carry the X-AgentBreeder-Center header that forces a CORS preflight.
//
// Errors are ApiError with a `kind`:
//   "unreachable": no HTTP answer at all (the console stopped, or this is the wrong address)
//   "session":     401, the session ended or never existed; sign in again
//   "http":        any other non-2xx answer; `code` and `message` come from the envelope

export class ApiError extends Error {
  constructor(kind, message, { status = 0, code = null, data = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

// An API path as the console's routes write them: segments of letters, digits, `_` and `-` (so no
// `.`/`..`, backslash or encoded slash that fetch would normalise out of /api/), then an optional
// query of already-encoded values.
const API_PATH = /^\/api(?:\/[A-Za-z0-9_-]+)+(?:\?[A-Za-z0-9_.~%=&-]*)?$/;

const UNREADABLE = { data: null, error: { code: "UNREADABLE", message: "Unreadable response" }, stale: false };

/**
 * `fetchImpl` is window.fetch (or a fake in tests); `session` is {get(), set(value)};
 * `onSessionEnded` runs once per 401 before the error is thrown.
 */
export function createClient({ fetchImpl, session, onSessionEnded = () => {} }) {
  async function request(path, { body, method } = {}) {
    if (typeof path !== "string" || !API_PATH.test(path)) throw new Error(`not an API path: ${path}`);
    const headers = { "X-AgentBreeder-Center": "1" };
    const token = session.get();
    if (token) headers["X-AgentBreeder-Session"] = token;
    const options = { headers, credentials: "omit", cache: "no-store", redirect: "error" };
    if (body !== undefined) {
      options.method = method || "POST";
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    } else if (method) {
      options.method = method;
    }
    let response;
    try {
      response = await fetchImpl(path, options);
    } catch {
      throw new ApiError("unreachable", "The console is not reachable at this address.");
    }
    let reply;
    try {
      reply = await response.json();
    } catch {
      reply = UNREADABLE;
    }
    if (!reply || typeof reply !== "object") reply = UNREADABLE;
    if (response.status === 401) {
      onSessionEnded();
      throw new ApiError("session", "The session ended. Sign in again.", { status: 401 });
    }
    if (!response.ok || reply === UNREADABLE) {
      const error = reply.error || {};
      throw new ApiError("http", error.message || `Request failed (${response.status})`, {
        status: response.status,
        code: error.code || null,
        data: reply.data ?? null,
      });
    }
    return { data: reply.data ?? null, error: reply.error ?? null, stale: Boolean(reply.stale) };
  }
  return {
    request,
    /** The envelope's data only. */
    async get(path) {
      return (await request(path)).data;
    },
    async post(path, body = {}) {
      return (await request(path, { body })).data;
    },
  };
}
