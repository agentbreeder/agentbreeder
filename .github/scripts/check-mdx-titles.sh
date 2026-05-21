#!/usr/bin/env bash
# .github/scripts/check-mdx-titles.sh
#
# Fails (exit 1) when two website/content/docs/*.mdx files share an identical
# frontmatter title: value. Guards against the <title> collision that a sloppy
# find-and-replace during a rebrand sweep can produce (Safeguard B of the
# Dashboard → Studio rebrand).
#
# Runs in CI from .github/workflows/docs-check.yml. Safe to run locally:
#   bash .github/scripts/check-mdx-titles.sh

set -euo pipefail

docs_dir="${1:-website/content/docs}"

if [ ! -d "$docs_dir" ]; then
  echo "::error::docs dir not found: $docs_dir"
  exit 2
fi

dupes="$(find "$docs_dir" -type f -name '*.mdx' -print0 \
  | xargs -0 grep -h '^title:' 2>/dev/null \
  | sort \
  | uniq -d || true)"

if [ -n "$dupes" ]; then
  echo "::error::Duplicate frontmatter title: lines found in $docs_dir/*.mdx"
  echo "$dupes" | sed 's/^/  /'
  echo
  echo "Two .mdx files now share the same title — fix by adding a distinguishing"
  echo "suffix (e.g. 'No Code — AgentBreeder Studio')."
  exit 1
fi

echo "All MDX frontmatter title: values are unique."
