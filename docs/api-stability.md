# API Stability & Versioning

AgentBreeder follows a versioned API with a clear deprecation policy. This page describes the version lifecycle, how to detect deprecated endpoints, and how to migrate when breaking changes land.

---

## API Versions

| Version | Status | Sunset Date | Notes |
|---------|--------|-------------|-------|
| **v1** | **Stable** (current) | No planned sunset | All existing integrations use v1 |
| **v2** | Preview | — | Cursor pagination, richer envelopes, batch ops |

All v1 endpoints are accessible at `/api/v1/…`. All v2 preview endpoints are at `/api/v2/…`.

---

## Stability Guarantees

### v1 (Stable)

- No breaking changes without a **minimum 12-month deprecation window**
- Additive changes (new optional fields, new endpoints) ship without version bumps
- Breaking changes (renamed/removed fields, changed types, different auth requirements) require a new version
- v1 will receive security fixes indefinitely

### v2 (Preview)

- Preview endpoints may change until v2 is declared GA
- GA declaration is announced in the release notes and ROADMAP
- Once declared GA, v2 gets the same 12-month deprecation guarantee as v1

### What counts as a breaking change?

| Change | Breaking? |
|--------|-----------|
| Adding a new optional request field | No |
| Adding a new response field | No |
| Adding a new endpoint | No |
| Removing a field from a response | **Yes** |
| Renaming a field | **Yes** |
| Changing a field type | **Yes** |
| Changing an HTTP status code | **Yes** |
| Changing auth requirements | **Yes** |
| Removing an endpoint | **Yes** |
| Changing pagination scheme | **Yes** |

---

## Response Headers

Every API response includes version metadata headers:

```
X-API-Version: v1
X-API-Latest: v1
```

When an endpoint is deprecated, additional headers appear:

```
Deprecation: date="2027-06-01"
Sunset: 2027-06-01
Link: </api/v2/agents>; rel="successor-version"
```

| Header | RFC | Meaning |
|--------|-----|---------|
| `Deprecation` | [RFC 8594](https://www.rfc-editor.org/rfc/rfc8594) | Date the endpoint was deprecated |
| `Sunset` | [RFC 8594](https://www.rfc-editor.org/rfc/rfc8594) | Date the endpoint will be removed |
| `Link` | — | URL of the replacement endpoint |

!!! tip "Monitor deprecation headers in CI"
    Add a test or a middleware check that fails your CI pipeline if a `Deprecation` header appears in API responses. This gives you advance warning before the sunset date.

---

## Deprecation Process

When a v1 endpoint is scheduled for removal:

1. **Announce** — release notes and CHANGELOG entry added
2. **Headers** — `Deprecation` and `Sunset` headers added to responses
3. **Docs** — endpoint page updated with a deprecation notice and migration guide
4. **12-month window** — endpoint remains fully functional
5. **Sunset** — endpoint removed; returns `410 Gone` with a JSON body pointing to the successor

```json
{
  "error": "endpoint_removed",
  "message": "This endpoint was removed on 2027-06-01. Use /api/v2/agents instead.",
  "successor": "/api/v2/agents",
  "docs": "https://www.agentbreeder.io/api-stability/"
}
```

---

## v2 Changes vs v1

### Pagination

v1 uses offset pagination:
```
GET /api/v1/agents?page=2&page_size=20
```

v2 uses cursor pagination (more efficient for large datasets):
```
GET /api/v2/agents?limit=20
GET /api/v2/agents?limit=20&cursor=<next_cursor from previous response>
```

Response:
```json
{
  "data": [...],
  "meta": {
    "api_version": "v2",
    "request_id": "f47ac10b-58cc-...",
    "next_cursor": "2026-03-13T12:00:00.000000"
  },
  "errors": []
}
```

### Batch Operations

v2 adds batch endpoints for bulk operations:

```bash
POST /api/v2/agents/batch
Content-Type: application/json

[
  { "name": "agent-a", "team": "eng", ... },
  { "name": "agent-b", "team": "eng", ... }
]
```

Each entry in the response has `data` (success) or `error` (partial failure), allowing bulk operations to succeed partially.

### Response Envelope

Every v2 response has a consistent envelope:

```json
{
  "data": { ... },
  "meta": {
    "api_version": "v2",
    "request_id": "uuid"
  },
  "errors": []
}
```

v1 also uses this envelope, but `meta` does not include `api_version` or `request_id`.

---

## Client Libraries

If you use the official Python or TypeScript SDKs, they handle versioning automatically. SDK versions are pinned to the corresponding API version:

| SDK version | API version |
|-------------|-------------|
| `agentbreeder-sdk < 2.0` | v1 |
| `agentbreeder-sdk >= 2.0` | v2 |
| `@agentbreeder/sdk < 2.0` | v1 |
| `@agentbreeder/sdk >= 2.0` | v2 |

---

## Questions?

Open an issue at [github.com/agentbreeder/agentbreeder](https://github.com/agentbreeder/agentbreeder/issues) or start a discussion at the Discussions tab.
