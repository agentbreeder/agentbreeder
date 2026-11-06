# Local onboarding verification plan

1. Inspect Docker Desktop and engine; start only if needed, avoid unrelated workload disruption.
2. Run the real local integration test through build, deployment, invocation, retry, state reopening and removal.
3. Add an example-only one-command helper around installed CLI. Retain private history, pin context, bound operations, recover owned deployment IDs on interruption and clean only the unique image tag.
4. Verify from a clean temporary installation; add failure recovery regressions, independent review, lint and full tests.
5. Make the verified demo the README entry point; retain manual own-image guide and troubleshooting. Record exact environment and evidence privately; do not mark production/UI or human onboarding validation complete.
