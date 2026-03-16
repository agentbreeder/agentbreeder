#!/usr/bin/env python3
"""
Create GitHub Issues for Agent Garden from docs/GITHUB_ISSUES.md.

Usage:
    GH_TOKEN=ghp_xxx python scripts/create_github_issues.py --dry-run
    GH_TOKEN=ghp_xxx python scripts/create_github_issues.py

Options:
    --dry-run   Print what would be created without making API calls.

Environment Variables:
    GH_TOKEN    GitHub personal access token with 'repo' scope.

The script:
    - Parses all 30 issues from docs/GITHUB_ISSUES.md
    - Creates labels (with colors) if they don't exist
    - Maps Priority (P0/P1/P2) to priority labels
    - Assigns milestones based on section headers
    - Skips issues that already exist (by title match)
    - Rate-limits API calls (1 second between requests)
    - Prints progress and a summary at the end
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' library is required. Install with: pip install requests")
    sys.exit(1)


REPO = "open-agent-garden/agent-garden"
API_BASE = f"https://api.github.com/repos/{REPO}"

# Label definitions: name -> color (hex without #)
LABEL_COLORS = {
    # Priority labels
    "priority:critical": "b60205",
    "priority:high": "d93f0b",
    "priority:medium": "fbca04",
    # Category labels
    "enhancement": "a2eeef",
    "templates": "0075ca",
    "launch": "e4e669",
    "marketing": "d876e3",
    "docs": "0075ca",
    "seo": "bfdadc",
    "community": "7057ff",
    "content": "c5def5",
    "engine": "1d76db",
    "cloud": "006b75",
    "epic": "b60205",
    "revenue": "0e8a16",
    "observability": "5319e7",
    "standards": "bfd4f2",
    "registry": "d4c5f9",
    "research": "c2e0c6",
    "enterprise": "f9d0c4",
    "dashboard": "fef2c0",
    "marketplace": "e99695",
    "legal": "ededed",
    "ip": "ededed",
    "business": "0e8a16",
    "fundraising": "fbca04",
}

# Priority -> label mapping
PRIORITY_LABELS = {
    "P0": "priority:critical",
    "P1": "priority:high",
    "P2": "priority:medium",
}

# Section -> milestone mapping
SECTION_MILESTONES = {
    "Growth & Launch Issues": "Growth & Launch",
    "Technical Build Issues": "Technical Build",
    "Legal & IP Issues": "Legal & IP",
    "Business Issues": "Business",
}


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def rate_limit_wait():
    """Wait 1 second between API calls to respect rate limits."""
    time.sleep(1)


def parse_issues(filepath: str) -> list[dict]:
    """Parse all issues from the GITHUB_ISSUES.md markdown file."""
    content = Path(filepath).read_text(encoding="utf-8")

    issues = []
    current_section = None

    # Split into lines for processing
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect section headers (## Growth & Launch Issues, etc.)
        section_match = re.match(r"^## (.+)$", line)
        if section_match:
            section_name = section_match.group(1).strip()
            if section_name in SECTION_MILESTONES:
                current_section = section_name
            i += 1
            continue

        # Detect issue headers (### Issue N: Title)
        issue_match = re.match(r"^### Issue \d+:\s*(.+)$", line)
        if issue_match:
            title = issue_match.group(1).strip()
            i += 1

            # Collect all lines until the next ### or ## or end of file
            issue_lines = []
            while i < len(lines):
                if re.match(r"^###? ", lines[i]):
                    break
                issue_lines.append(lines[i])
                i += 1

            issue_text = "\n".join(issue_lines)
            issue = parse_single_issue(title, issue_text, current_section)
            issues.append(issue)
            continue

        i += 1

    return issues


def parse_single_issue(title: str, text: str, section: str | None) -> dict:
    """Parse a single issue's metadata and body from its text block."""
    # Extract priority
    priority = None
    priority_match = re.search(r"\*\*Priority:\*\*\s*(P[012])", text)
    if priority_match:
        priority = priority_match.group(1)

    # Extract labels
    labels = []
    labels_match = re.search(r"\*\*Labels:\*\*\s*(.+)", text)
    if labels_match:
        raw = labels_match.group(1)
        labels = [label.strip().strip("`") for label in raw.split(",")]

    # Extract effort
    effort = ""
    effort_match = re.search(r"\*\*Estimated Effort:\*\*\s*(.+)", text)
    if effort_match:
        effort = effort_match.group(1).strip()

    # Build the body: Description + Acceptance Criteria + Effort
    # Extract description section
    desc_match = re.search(
        r"\*\*Description:\*\*\s*\n(.*?)(?=\*\*Acceptance Criteria:\*\*)",
        text,
        re.DOTALL,
    )
    description = desc_match.group(1).strip() if desc_match else ""

    # Extract acceptance criteria
    ac_match = re.search(
        r"\*\*Acceptance Criteria:\*\*\s*\n(.*?)(?=\*\*Estimated Effort:\*\*)",
        text,
        re.DOTALL,
    )
    acceptance_criteria = ac_match.group(1).strip() if ac_match else ""

    # Build GitHub issue body
    body_parts = []
    if description:
        body_parts.append(f"## Description\n\n{description}")
    if acceptance_criteria:
        body_parts.append(f"## Acceptance Criteria\n\n{acceptance_criteria}")
    if effort:
        body_parts.append(f"## Estimated Effort\n\n{effort}")
    if priority:
        body_parts.append(f"**Priority:** {priority}")

    body = "\n\n".join(body_parts)

    # Add priority label
    all_labels = list(labels)
    if priority and priority in PRIORITY_LABELS:
        all_labels.append(PRIORITY_LABELS[priority])

    return {
        "title": title,
        "body": body,
        "labels": all_labels,
        "priority": priority,
        "effort": effort,
        "section": section,
        "milestone": SECTION_MILESTONES.get(section) if section else None,
    }


def get_existing_issues(headers: dict) -> set[str]:
    """Fetch all existing issue titles from the repo (handles pagination)."""
    titles = set()
    page = 1
    while True:
        resp = requests.get(
            f"{API_BASE}/issues",
            headers=headers,
            params={"state": "all", "per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        issues = resp.json()
        if not issues:
            break
        for issue in issues:
            titles.add(issue["title"])
        if len(issues) < 100:
            break
        page += 1
        rate_limit_wait()
    return titles


def get_existing_labels(headers: dict) -> set[str]:
    """Fetch all existing label names from the repo."""
    labels = set()
    page = 1
    while True:
        resp = requests.get(
            f"{API_BASE}/labels",
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        for label in data:
            labels.add(label["name"])
        if len(data) < 100:
            break
        page += 1
        rate_limit_wait()
    return labels


def create_label(name: str, color: str, headers: dict, dry_run: bool) -> bool:
    """Create a label if it doesn't exist. Returns True if created."""
    if dry_run:
        print(f"  [DRY RUN] Would create label: '{name}' (#{color})")
        return True

    resp = requests.post(
        f"{API_BASE}/labels",
        headers=headers,
        json={"name": name, "color": color},
        timeout=30,
    )
    if resp.status_code == 201:
        print(f"  Created label: '{name}' (#{color})")
        return True
    elif resp.status_code == 422:
        # Already exists (race condition or not in our cache)
        return False
    else:
        print(f"  WARNING: Failed to create label '{name}': {resp.status_code} {resp.text}")
        return False


def get_or_create_milestones(
    milestone_names: set[str], headers: dict, dry_run: bool
) -> dict[str, int]:
    """Get existing milestones or create new ones. Returns name -> number mapping."""
    milestones = {}

    if not dry_run:
        # Fetch existing milestones
        resp = requests.get(
            f"{API_BASE}/milestones",
            headers=headers,
            params={"state": "open", "per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        for m in resp.json():
            milestones[m["title"]] = m["number"]
        rate_limit_wait()

    # Create missing milestones
    for name in milestone_names:
        if name in milestones:
            print(f"  Milestone exists: '{name}' (#{milestones[name]})")
            continue

        if dry_run:
            print(f"  [DRY RUN] Would create milestone: '{name}'")
            milestones[name] = 0  # Placeholder
        else:
            resp = requests.post(
                f"{API_BASE}/milestones",
                headers=headers,
                json={"title": name},
                timeout=30,
            )
            if resp.status_code == 201:
                milestones[name] = resp.json()["number"]
                print(f"  Created milestone: '{name}' (#{milestones[name]})")
            elif resp.status_code == 422:
                # Already exists, re-fetch
                resp2 = requests.get(
                    f"{API_BASE}/milestones",
                    headers=headers,
                    params={"state": "open", "per_page": 100},
                    timeout=30,
                )
                resp2.raise_for_status()
                for m in resp2.json():
                    if m["title"] == name:
                        milestones[name] = m["number"]
                        break
            else:
                print(f"  WARNING: Failed to create milestone '{name}': {resp.status_code}")
            rate_limit_wait()

    return milestones


def create_issue(
    issue: dict,
    milestone_map: dict[str, int],
    headers: dict,
    dry_run: bool,
) -> bool:
    """Create a single GitHub issue. Returns True if created."""
    title = issue["title"]
    payload = {
        "title": title,
        "body": issue["body"],
        "labels": issue["labels"],
    }

    milestone_name = issue.get("milestone")
    if milestone_name and milestone_name in milestone_map:
        milestone_num = milestone_map[milestone_name]
        if milestone_num > 0:
            payload["milestone"] = milestone_num

    if dry_run:
        print(f"  [DRY RUN] Would create issue: '{title}'")
        print(f"            Labels: {issue['labels']}")
        if milestone_name:
            print(f"            Milestone: {milestone_name}")
        print()
        return True

    resp = requests.post(
        f"{API_BASE}/issues",
        headers=headers,
        json=payload,
        timeout=30,
    )
    if resp.status_code == 201:
        number = resp.json()["number"]
        print(f"  Created issue #{number}: '{title}'")
        return True
    else:
        print(f"  ERROR: Failed to create issue '{title}': {resp.status_code} {resp.text}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Create GitHub issues for Agent Garden from docs/GITHUB_ISSUES.md"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making API calls",
    )
    args = parser.parse_args()

    # Resolve the markdown file path relative to the script location
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    issues_file = repo_root / "docs" / "GITHUB_ISSUES.md"

    if not issues_file.exists():
        print(f"Error: {issues_file} not found")
        sys.exit(1)

    token = os.environ.get("GH_TOKEN")
    if not token and not args.dry_run:
        print("Error: GH_TOKEN environment variable is required (unless using --dry-run)")
        sys.exit(1)

    headers = get_headers(token) if token else {}

    # --- Parse issues ---
    print(f"Parsing issues from {issues_file}...")
    issues = parse_issues(str(issues_file))
    print(f"Found {len(issues)} issues.\n")

    if not issues:
        print("No issues found. Check the markdown format.")
        sys.exit(1)

    # --- Collect all needed labels and milestones ---
    all_labels = set()
    all_milestones = set()
    for issue in issues:
        all_labels.update(issue["labels"])
        if issue.get("milestone"):
            all_milestones.add(issue["milestone"])

    # --- Ensure labels exist ---
    print("--- Labels ---")
    if not args.dry_run:
        existing_labels = get_existing_labels(headers)
        rate_limit_wait()
    else:
        existing_labels = set()

    labels_created = 0
    for label in sorted(all_labels):
        if label in existing_labels:
            print(f"  Label exists: '{label}'")
            continue
        color = LABEL_COLORS.get(label, "cccccc")
        if create_label(label, color, headers, args.dry_run):
            labels_created += 1
        if not args.dry_run:
            rate_limit_wait()

    print(f"\nLabels created: {labels_created}")
    print()

    # --- Ensure milestones exist ---
    print("--- Milestones ---")
    milestone_map = get_or_create_milestones(all_milestones, headers, args.dry_run)
    print()

    # --- Check existing issues ---
    print("--- Issues ---")
    if not args.dry_run:
        print("Fetching existing issues...")
        existing_titles = get_existing_issues(headers)
        rate_limit_wait()
        print(f"Found {len(existing_titles)} existing issues.\n")
    else:
        existing_titles = set()

    # --- Create issues ---
    created = 0
    skipped = 0
    failed = 0

    for i, issue in enumerate(issues, 1):
        title = issue["title"]
        if title in existing_titles:
            print(f"  [{i}/{len(issues)}] SKIP (exists): '{title}'")
            skipped += 1
            continue

        print(f"  [{i}/{len(issues)}] Creating: '{title}'")
        if create_issue(issue, milestone_map, headers, args.dry_run):
            created += 1
        else:
            failed += 1

        if not args.dry_run:
            rate_limit_wait()

    # --- Summary ---
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total issues parsed:  {len(issues)}")
    print(f"  Created:              {created}")
    print(f"  Skipped (existing):   {skipped}")
    print(f"  Failed:               {failed}")
    print(f"  Labels created:       {labels_created}")
    print(f"  Milestones:           {len(milestone_map)}")
    if args.dry_run:
        print("\n  (DRY RUN -- no changes were made)")
    print()


if __name__ == "__main__":
    main()
