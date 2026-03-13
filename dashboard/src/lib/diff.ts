/**
 * Simple LCS-based line diff algorithm.
 * No external dependencies — self-contained for Agent Garden.
 */

export type DiffLineType = "added" | "removed" | "unchanged";

export interface DiffLine {
  type: DiffLineType;
  content: string;
  /** Line number in the "before" text (1-based), null for added lines */
  oldLineNumber: number | null;
  /** Line number in the "after" text (1-based), null for removed lines */
  newLineNumber: number | null;
}

/**
 * Compute the LCS (Longest Common Subsequence) table for two arrays of strings.
 * Returns a 2D array where lcs[i][j] = length of LCS of a[0..i-1] and b[0..j-1].
 */
function buildLcsTable(a: string[], b: string[]): number[][] {
  const m = a.length;
  const n = b.length;
  const table: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0)
  );

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (a[i - 1] === b[j - 1]) {
        table[i][j] = table[i - 1][j - 1] + 1;
      } else {
        table[i][j] = Math.max(table[i - 1][j], table[i][j - 1]);
      }
    }
  }

  return table;
}

/**
 * Backtrack through the LCS table to produce a diff.
 */
function backtrack(
  table: number[][],
  a: string[],
  b: string[],
  i: number,
  j: number
): DiffLine[] {
  const result: DiffLine[] = [];

  // Use iterative approach to avoid stack overflow on large inputs
  let ci = i;
  let cj = j;

  // We'll collect in reverse order, then reverse at the end
  const stack: DiffLine[] = [];

  while (ci > 0 || cj > 0) {
    if (ci > 0 && cj > 0 && a[ci - 1] === b[cj - 1]) {
      stack.push({
        type: "unchanged",
        content: a[ci - 1],
        oldLineNumber: ci,
        newLineNumber: cj,
      });
      ci--;
      cj--;
    } else if (cj > 0 && (ci === 0 || table[ci][cj - 1] >= table[ci - 1][cj])) {
      stack.push({
        type: "added",
        content: b[cj - 1],
        oldLineNumber: null,
        newLineNumber: cj,
      });
      cj--;
    } else if (ci > 0) {
      stack.push({
        type: "removed",
        content: a[ci - 1],
        oldLineNumber: ci,
        newLineNumber: null,
      });
      ci--;
    }
  }

  // Reverse since we collected bottom-up
  for (let k = stack.length - 1; k >= 0; k--) {
    result.push(stack[k]);
  }

  return result;
}

/**
 * Compute a line-by-line diff between two strings.
 *
 * @param before - The original text
 * @param after - The modified text
 * @returns Array of DiffLine entries describing the changes
 */
export function computeDiff(before: string, after: string): DiffLine[] {
  const aLines = before.split("\n");
  const bLines = after.split("\n");

  const table = buildLcsTable(aLines, bLines);
  return backtrack(table, aLines, bLines, aLines.length, bLines.length);
}

/**
 * A "hunk" of consecutive diff lines, used for collapsing unchanged sections.
 */
export interface DiffHunk {
  type: "changed" | "unchanged";
  lines: DiffLine[];
}

/**
 * Group diff lines into hunks of changed and unchanged lines.
 * Unchanged hunks longer than `contextLines * 2` will be collapsed,
 * keeping `contextLines` at the top and bottom as context.
 */
export function groupIntoHunks(
  diffLines: DiffLine[],
  _contextLines = 3
): DiffHunk[] {
  if (diffLines.length === 0) return [];

  const hunks: DiffHunk[] = [];
  let currentLines: DiffLine[] = [];
  let currentType: "changed" | "unchanged" | null = null;

  for (const line of diffLines) {
    const lineType: "changed" | "unchanged" =
      line.type === "unchanged" ? "unchanged" : "changed";

    if (currentType === null) {
      currentType = lineType;
      currentLines = [line];
    } else if (lineType === currentType) {
      currentLines.push(line);
    } else {
      hunks.push({ type: currentType, lines: currentLines });
      currentType = lineType;
      currentLines = [line];
    }
  }

  if (currentLines.length > 0 && currentType !== null) {
    hunks.push({ type: currentType, lines: currentLines });
  }

  return hunks;
}
