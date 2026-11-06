# Local deployment implementation

1. Extend immutable image contract to local image IDs; test local-only validation.
2. Add private SQLite store with atomic state/events and single-writer lock.
3. Add bounded local Docker adapter with ownership checks and loopback health verification.
4. Add shared lifecycle service and CLI deploy/status/remove, retaining validation and doctor.
5. Test persistence, authorization, retries, collisions, failure states and cleanup; add opt-in real Docker integration.
6. Review implementation independently; fix findings, rerun verification and update private execution state. Do not publish or claim production support.
