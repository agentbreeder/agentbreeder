import type { JSX } from "react";

/**
 * Visual indicator that a feature is scaffolded but not yet fully wired
 * to a real backend implementation. Renders inline next to the partial /
 * stub UI element it describes.
 *
 * For full-page stubs, use {@link ComingSoonBanner} instead.
 */
export function ComingSoonBadge({
  feature,
  issue,
}: {
  feature: string;
  issue: string;
}): JSX.Element {
  const issueNumber = issue.replace("#", "");
  return (
    <span
      title={`${feature} — tracked at ${issue}`}
      className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-300"
    >
      <span aria-hidden>⏳</span>
      Coming soon
      <a
        href={`https://github.com/agentbreeder/agentbreeder/issues/${issueNumber}`}
        className="underline decoration-dotted"
        target="_blank"
        rel="noopener noreferrer"
      >
        {issue.startsWith("#") ? issue : `#${issue}`}
      </a>
    </span>
  );
}

/**
 * Top-of-page banner rendered when an entire dashboard route is a
 * placeholder / mock-data scaffold. Always include a link to the tracking
 * issue so users know when to expect the real implementation.
 */
export function ComingSoonBanner({
  feature,
  issue,
  description,
}: {
  feature: string;
  issue: string;
  description?: string;
}): JSX.Element {
  const issueNumber = issue.replace("#", "");
  return (
    <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-800 dark:text-amber-200">
      <div className="flex items-start gap-3">
        <span aria-hidden className="mt-0.5">⏳</span>
        <div className="flex-1">
          <p className="font-semibold">Coming soon — {feature}</p>
          <p className="mt-1 text-amber-700/90 dark:text-amber-300/90">
            {description ??
              "This page is scaffolded with mock data. Persistence, real backend wiring, and full interactivity are still under construction."}{" "}
            Track progress at{" "}
            <a
              href={`https://github.com/agentbreeder/agentbreeder/issues/${issueNumber}`}
              className="underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              {issue.startsWith("#") ? issue : `#${issue}`}
            </a>
            .
          </p>
        </div>
      </div>
    </div>
  );
}
