/**
 * Submit for Review — dialog + button for submitting a resource from a builder
 * page. Creates a branch, commits the content, and opens a PR.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import {
  GitPullRequest,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { useToast } from "@/hooks/use-toast";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SubmitForReviewProps {
  /** Resource type: agent, prompt, tool, mcp, rag, memory */
  resourceType: string;
  /** Name of the resource */
  resourceName: string;
  /** YAML or JSON content to commit */
  content: string;
  /** File path within the repo to commit */
  filePath?: string;
  /** Button variant */
  variant?: "default" | "outline" | "ghost";
  /** Additional class name */
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SubmitForReview({
  resourceType,
  resourceName,
  content,
  filePath,
  variant = "outline",
  className,
}: SubmitForReviewProps) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const navigate = useNavigate();
  const { user } = useAuth();
  const { toast } = useToast();

  const userName = user?.email ?? "anonymous";
  const defaultTitle = `Update ${resourceType}: ${resourceName}`;
  const defaultFilePath =
    filePath ?? `${resourceType}s/${resourceName}.yaml`;

  const submitMut = useMutation({
    mutationFn: async () => {
      // 1. Create a branch
      const branchRes = await api.git.createBranch({
        user: userName,
        resource_type: resourceType,
        resource_name: resourceName,
      });
      const branch = branchRes.data.branch;

      // 2. Commit the content
      await api.git.commit({
        branch,
        file_path: defaultFilePath,
        content,
        message: title || defaultTitle,
        author: userName,
      });

      // 3. Create the PR
      const prRes = await api.git.prs.create({
        branch,
        title: title || defaultTitle,
        description,
        submitter: userName,
      });

      return prRes.data;
    },
    onSuccess: (pr) => {
      toast({
        title: "Submitted for review",
        description: `PR "${pr.title}" created`,
        variant: "success",
      });
      setOpen(false);
      navigate(`/approvals/${pr.id}`);
    },
    onError: (e: Error) => {
      toast({
        title: "Submit failed",
        description: e.message,
        variant: "error",
      });
    },
  });

  return (
    <>
      <Button
        size="sm"
        variant={variant}
        className={className}
        onClick={() => {
          setTitle(defaultTitle);
          setDescription("");
          setOpen(true);
        }}
      >
        <GitPullRequest className="size-3.5" />
        Submit for Review
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Submit for Review</DialogTitle>
            <DialogDescription>
              This will create a pull request for{" "}
              <span className="font-medium capitalize">{resourceType}</span>{" "}
              <span className="font-medium">{resourceName}</span>. A reviewer
              must approve before it is published to the registry.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 py-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Title
              </label>
              <Input
                value={title}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)}
                placeholder={defaultTitle}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                Description (optional)
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe the changes..."
                rows={3}
                className="w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:border-ring"
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => submitMut.mutate()}
              disabled={submitMut.isPending}
            >
              {submitMut.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <GitPullRequest className="size-3.5" />
              )}
              Submit
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
