# Verified local onboarding

Follow-up to #648: prove the existing local lifecycle and remove manual placeholder editing from sample onboarding. User authorized build → deploy → invoke → retry → remove and onboarding simplification.

Add an example-only quickstart script that invokes the installed CLI (not another deployment service). It checks Docker before building, builds a uniquely tagged sample image, generates a manifest with the real image ID, deploys, invokes, retries and verifies identity reuse, reads history and removes the owned deployment. Every external call has a timeout. Print milestones and retained private state location. Clean only its unique deployment/tag; retain state for recovery on failure. Never claim failed cleanup succeeded. The demo has no model or authentication. No product commands or production surface added.

Acceptance: one documented command runs through the real installed CLI; Docker failure gives prerequisite guidance before build; failures return nonzero; successful run verifies invocation response, same ID on retry, durable events and removed state. Document manual commands for running a user's own container separately. No production milestone completion implied.
