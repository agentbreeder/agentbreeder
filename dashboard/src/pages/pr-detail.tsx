/**
 * PR Detail / Review Page — side-by-side YAML diff, comments, action buttons,
 * status timeline, and environment promotion.
 */

import { useState, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  AlertCircle,
  MessageSquare,
  GitPullRequest,
  GitMerge,
  Clock,
  Send,
  Loader2,
  Tag,
  User,
  ChevronRight,
  FileCode,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, type PRStatus, type GitPR, type GitDiffEntry, type GitPRComment } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EnvironmentPromotion } from "@/components/environment-promotion";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";

// ---------------------------------------------------------------------------
// Status config
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<
  PRStatus,
  { label: string; color: string; icon: React.ComponentType<{ className?: string }> }
> = {
  draft: { label: "Draft", color: "text-muted-foreground", icon: Clock },
  submitted: { label: "Submitted", color: "text-blue-600 dark:text-blue-400", icon: GitPullRequest },
  in_review: { label: "In Review", color: "text-amber-600 dark:text-amber-400", icon: AlertCircle },
  approved: { label: "Approved", color: "text-green-600 dark:text-green-400", icon: CheckCircle2 },
  changes_requested: { label: "Changes Requested", color: "text-orange-600 dark:text-orange-400", icon: AlertCircle },
  rejected: { label: "Rejected", color: "text-red-600 dark:text-red-400", icon: XCircle },
  published: { label: "Published", color: "text-emerald-600 dark:text-emerald-400", icon: CheckCircle2 },
};

const STATUS_TIMELINE: PRStatus[] = [
  "submitted",
  "in_review",
  "approved",
  "published",
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Diff Viewer
// ---------------------------------------------------------------------------

function DiffViewer({ files }: { files: GitDiffEntry[] }) {
  if (files.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed border-border py-12 text-sm text-muted-foreground">
        No changes detected
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {files.map((file) => (
        <div key={file.file_path} className="overflow-hidden rounded-lg border border-border">
          {/* File header */}
          <div className="flex items-center gap-2 border-b border-border bg-muted/50 px-3 py-2">
            <FileCode className="size-3.5 text-muted-foreground" />
            <span className="text-xs font-medium">{file.file_path}</span>
            <Badge variant="outline" className="ml-auto text-[10px]">
              {file.status}
            </Badge>
          </div>

          {/* Diff content — side-by-side */}
          {file.diff_text ? (
            <div className="overflow-x-auto">
              <DiffBlock diffText={file.diff_text} />
            </div>
          ) : (
            <div className="px-3 py-4 text-xs text-muted-foreground">
              Binary file or no textual diff available
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function DiffBlock({ diffText }: { diffText: string }) {
  const lines = diffText.split("\n");

  return (
    <pre className="text-xs leading-relaxed">
      {lines.map((line, i) => {
        let bgClass = "";
        let textClass = "text-muted-foreground";

        if (line.startsWith("+") && !line.startsWith("+++")) {
          bgClass = "bg-green-500/10";
          textClass = "text-green-700 dark:text-green-400";
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          bgClass = "bg-red-500/10";
          textClass = "text-red-700 dark:text-red-400";
        } else if (line.startsWith("@@")) {
          bgClass = "bg-blue-500/5";
          textClass = "text-blue-600 dark:text-blue-400";
        }

        return (
          <div
            key={i}
            className={cn("px-3 py-0.5 font-mono", bgClass, textClass)}
          >
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Comment Thread
// ---------------------------------------------------------------------------

function CommentThread({
  comments,
  prId,
}: {
  comments: GitPRComment[];
  prId: string;
}) {
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const addComment = useMutation({
    mutationFn: () =>
      api.git.prs.addComment(prId, user?.email ?? "anonymous", text),
    onSuccess: () => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["pr", prId] });
    },
  });

  return (
    <div className="space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium">
        <MessageSquare className="size-4" />
        Comments ({comments.length})
      </h3>

      {comments.length > 0 ? (
        <div className="space-y-2">
          {comments.map((c) => (
            <div key={c.id} className="rounded-lg border border-border bg-muted/30 p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <User className="size-3" />
                <span className="font-medium text-foreground">{c.author}</span>
                <span>{formatDate(c.created_at)}</span>
              </div>
              <p className="mt-1.5 text-sm">{c.text}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No comments yet</p>
      )}

      {/* Add comment */}
      <div className="flex gap-2">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Add a comment..."
          rows={2}
          className="flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:border-ring"
        />
        <Button
          size="sm"
          variant="outline"
          className="h-auto self-end"
          disabled={!text.trim() || addComment.isPending}
          onClick={() => addComment.mutate()}
        >
          {addComment.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Send className="size-3.5" />
          )}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status Timeline
// ---------------------------------------------------------------------------

function StatusTimeline({ currentStatus }: { currentStatus: PRStatus }) {
  const currentIdx = STATUS_TIMELINE.indexOf(currentStatus);
  const isTerminalBad =
    currentStatus === "rejected" || currentStatus === "changes_requested";

  return (
    <div className="flex items-center gap-1">
      {STATUS_TIMELINE.map((s, i) => {
        const cfg = STATUS_CONFIG[s];
        const Icon = cfg.icon;
        const isActive = s === currentStatus;
        const isPast = currentIdx >= 0 && i < currentIdx;
        const isReached = isActive || isPast;

        return (
          <div key={s} className="flex items-center gap-1">
            {i > 0 && (
              <ChevronRight
                className={cn(
                  "size-3",
                  isReached ? "text-foreground/40" : "text-muted-foreground/30"
                )}
              />
            )}
            <div
              className={cn(
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors",
                isActive && !isTerminalBad
                  ? "bg-foreground/10 text-foreground"
                  : isPast
                    ? "text-muted-foreground"
                    : "text-muted-foreground/40"
              )}
            >
              <Icon className="size-3" />
              {cfg.label}
            </div>
          </div>
        );
      })}

      {/* Show terminal-bad status separately */}
      {isTerminalBad && (
        <>
          <ChevronRight className="size-3 text-muted-foreground/30" />
          <div className="flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-600 dark:text-red-400">
            {STATUS_CONFIG[currentStatus].label}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Review Actions
// ---------------------------------------------------------------------------

function ReviewActions({ pr }: { pr: GitPR }) {
  const { user } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [tagVersion, setTagVersion] = useState("");

  const reviewer = user?.email ?? "reviewer";

  const approveMut = useMutation({
    mutationFn: () => api.git.prs.approve(pr.id, reviewer),
    onSuccess: () => {
      toast({ title: "PR Approved", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["pr", pr.id] });
    },
    onError: (e: Error) => toast({ title: "Failed to approve", description: e.message, variant: "error" }),
  });

  const rejectMut = useMutation({
    mutationFn: () => api.git.prs.reject(pr.id, reviewer, rejectReason),
    onSuccess: () => {
      toast({ title: "PR Rejected", variant: "info" });
      setShowRejectForm(false);
      queryClient.invalidateQueries({ queryKey: ["pr", pr.id] });
    },
    onError: (e: Error) => toast({ title: "Failed to reject", description: e.message, variant: "error" }),
  });

  const mergeMut = useMutation({
    mutationFn: () => api.git.prs.merge(pr.id, tagVersion || undefined),
    onSuccess: () => {
      toast({ title: "PR Merged & Published", variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["pr", pr.id] });
      queryClient.invalidateQueries({ queryKey: ["prs"] });
    },
    onError: (e: Error) => toast({ title: "Failed to merge", description: e.message, variant: "error" }),
  });

  const canReview =
    pr.status === "submitted" || pr.status === "in_review";
  const canMerge = pr.status === "approved";

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">Actions</h3>

      {canReview && (
        <div className="space-y-2">
          <Button
            size="sm"
            className="w-full gap-1.5"
            onClick={() => approveMut.mutate()}
            disabled={approveMut.isPending}
          >
            {approveMut.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="size-3.5" />
            )}
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="w-full gap-1.5 text-orange-600 hover:text-orange-700 dark:text-orange-400"
            onClick={() => setShowRejectForm(!showRejectForm)}
          >
            <AlertCircle className="size-3.5" />
            Request Changes
          </Button>
          <Button
            size="sm"
            variant="destructive"
            className="w-full gap-1.5"
            onClick={() => {
              if (!rejectReason.trim()) {
                setShowRejectForm(true);
                return;
              }
              rejectMut.mutate();
            }}
            disabled={rejectMut.isPending}
          >
            {rejectMut.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <XCircle className="size-3.5" />
            )}
            Reject
          </Button>

          {showRejectForm && (
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Reason for rejection or requested changes..."
              rows={3}
              className="w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:border-ring"
            />
          )}
        </div>
      )}

      {canMerge && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Tag className="size-3.5 text-muted-foreground" />
            <input
              value={tagVersion}
              onChange={(e) => setTagVersion(e.target.value)}
              placeholder="Tag version (e.g. 1.0.0)"
              className="h-7 flex-1 rounded-md border border-border bg-background px-2 text-xs outline-none placeholder:text-muted-foreground focus:border-ring"
            />
          </div>
          <Button
            size="sm"
            className="w-full gap-1.5 bg-green-600 text-white hover:bg-green-700 dark:bg-green-700 dark:hover:bg-green-600"
            onClick={() => mergeMut.mutate()}
            disabled={mergeMut.isPending}
          >
            {mergeMut.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <GitMerge className="size-3.5" />
            )}
            Merge & Publish
          </Button>
        </div>
      )}

      {pr.status === "published" && (
        <div className="rounded-md border border-green-500/20 bg-green-500/5 p-3 text-xs text-green-700 dark:text-green-400">
          <div className="flex items-center gap-1.5 font-medium">
            <CheckCircle2 className="size-3.5" />
            Published
          </div>
          {pr.tag && (
            <p className="mt-1 text-muted-foreground">
              Tagged as <code className="rounded bg-muted px-1">{pr.tag}</code>
            </p>
          )}
        </div>
      )}

      {pr.status === "rejected" && pr.reject_reason && (
        <div className="rounded-md border border-red-500/20 bg-red-500/5 p-3 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-red-700 dark:text-red-400">
            <XCircle className="size-3.5" />
            Rejected
          </div>
          <p className="mt-1 text-muted-foreground">{pr.reject_reason}</p>
        </div>
      )}

      {pr.status === "changes_requested" && pr.reject_reason && (
        <div className="rounded-md border border-orange-500/20 bg-orange-500/5 p-3 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-orange-700 dark:text-orange-400">
            <AlertCircle className="size-3.5" />
            Changes Requested
          </div>
          <p className="mt-1 text-muted-foreground">{pr.reject_reason}</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function PRDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["pr", id],
    queryFn: () => api.git.prs.get(id!),
    enabled: !!id,
  });

  const pr = data?.data;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!pr || error) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <p className="text-sm text-muted-foreground">Pull request not found.</p>
        <Link to="/approvals" className="mt-2 text-sm text-blue-600 hover:underline">
          Back to Approvals
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl p-6">
      {/* Back link */}
      <Link
        to="/approvals"
        className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" />
        Back to Approvals
      </Link>

      {/* Header */}
      <div className="mb-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold">{pr.title}</h1>
            <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
              <span>by {pr.submitter}</span>
              <span>on branch {pr.branch}</span>
              <span>{formatDate(pr.created_at)}</span>
            </div>
            {pr.description && (
              <p className="mt-2 text-sm text-muted-foreground">{pr.description}</p>
            )}
          </div>
        </div>

        {/* Status timeline */}
        <div className="mt-4">
          <StatusTimeline currentStatus={pr.status} />
        </div>
      </div>

      {/* Main grid: diff + sidebar */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_280px]">
        {/* Left: Diff + Comments */}
        <div className="space-y-6 min-w-0">
          {/* Diff */}
          <div>
            <h2 className="mb-3 flex items-center gap-2 text-sm font-medium">
              <FileCode className="size-4" />
              Changes
              {pr.diff?.stats && (
                <span className="text-xs font-normal text-muted-foreground">
                  {pr.diff.stats}
                </span>
              )}
            </h2>
            <DiffViewer files={pr.diff?.files ?? []} />
          </div>

          {/* Commit history */}
          {pr.commits.length > 0 && (
            <div>
              <h3 className="mb-2 text-sm font-medium">
                Commits ({pr.commits.length})
              </h3>
              <div className="space-y-1">
                {pr.commits.map((c) => (
                  <div
                    key={c.sha}
                    className="flex items-center gap-3 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs"
                  >
                    <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                      {c.sha.slice(0, 7)}
                    </code>
                    <span className="min-w-0 truncate">{c.message}</span>
                    <span className="ml-auto shrink-0 text-muted-foreground">
                      {c.author}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Comments */}
          <CommentThread comments={pr.comments} prId={pr.id} />

          {/* Environment Promotion (shown when approved or published) */}
          {(pr.status === "approved" || pr.status === "published") && (
            <EnvironmentPromotion
              resourceType={pr.resource_type}
              resourceName={pr.resource_name}
              tag={pr.tag}
              prId={pr.id}
              status={pr.status}
            />
          )}
        </div>

        {/* Right sidebar: Actions */}
        <aside className="space-y-6">
          <ReviewActions pr={pr} />

          {/* Meta info */}
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Details</h3>
            <dl className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Resource Type</dt>
                <dd className="capitalize font-medium">{pr.resource_type || "N/A"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Resource</dt>
                <dd className="font-medium">{pr.resource_name || "N/A"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Branch</dt>
                <dd className="truncate max-w-[160px] font-mono text-[11px]">
                  {pr.branch}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Updated</dt>
                <dd>{formatDate(pr.updated_at)}</dd>
              </div>
              {pr.reviewer && (
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Reviewer</dt>
                  <dd>{pr.reviewer}</dd>
                </div>
              )}
            </dl>
          </div>
        </aside>
      </div>
    </div>
  );
}
