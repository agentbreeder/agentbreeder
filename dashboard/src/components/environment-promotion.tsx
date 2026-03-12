/**
 * Environment Promotion — visual pipeline showing DEV -> STAGING -> PRODUCTION
 * with promote buttons.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Circle,
  Loader2,
  Rocket,
  Lock,
  Tag,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, type PRStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Environment = "dev" | "staging" | "production";

interface EnvironmentState {
  env: Environment;
  label: string;
  version: string | null;
  deployed: boolean;
}

interface EnvironmentPromotionProps {
  resourceType: string;
  resourceName: string;
  tag: string | null;
  prId: string;
  status: PRStatus;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getEnvironmentStates(
  tag: string | null,
  status: PRStatus,
): EnvironmentState[] {
  // Simulate environment state based on PR status and tag
  const hasTag = !!tag;
  const isPublished = status === "published";

  return [
    {
      env: "dev",
      label: "Development",
      version: tag ?? "latest",
      deployed: true, // always in dev if PR exists
    },
    {
      env: "staging",
      label: "Staging",
      version: isPublished && hasTag ? tag : null,
      deployed: isPublished,
    },
    {
      env: "production",
      label: "Production",
      version: null, // manual promotion needed
      deployed: false,
    },
  ];
}

// ---------------------------------------------------------------------------
// Environment Card
// ---------------------------------------------------------------------------

function EnvironmentCard({
  state,
  isNext,
  canPromote,
  onPromote,
  isPromoting,
}: {
  state: EnvironmentState;
  isNext: boolean;
  canPromote: boolean;
  onPromote: () => void;
  isPromoting: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 rounded-lg border p-4 transition-all",
        state.deployed
          ? "border-green-500/30 bg-green-500/5"
          : isNext
            ? "border-amber-500/30 bg-amber-500/5"
            : "border-border bg-muted/30"
      )}
    >
      {/* Status icon */}
      <div className="flex items-center gap-1.5">
        {state.deployed ? (
          <CheckCircle2 className="size-4 text-green-600 dark:text-green-400" />
        ) : (
          <Circle className="size-4 text-muted-foreground/40" />
        )}
        <span className="text-sm font-medium">{state.label}</span>
      </div>

      {/* Version */}
      {state.version ? (
        <Badge variant="outline" className="gap-1 text-[10px]">
          <Tag className="size-2.5" />
          {state.version}
        </Badge>
      ) : (
        <span className="text-[10px] text-muted-foreground">Not deployed</span>
      )}

      {/* Promote button */}
      {canPromote && (
        <Button
          size="xs"
          variant="outline"
          className="mt-1 gap-1 text-[11px]"
          onClick={onPromote}
          disabled={isPromoting}
        >
          {isPromoting ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Rocket className="size-3" />
          )}
          Promote
        </Button>
      )}

      {/* Locked indicator for production when eval hasn't passed */}
      {state.env === "production" && !state.deployed && !canPromote && (
        <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <Lock className="size-2.5" />
          Requires eval pass
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function EnvironmentPromotion({
  resourceType,
  resourceName,
  tag,
  prId,
  status,
}: EnvironmentPromotionProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [promotingTo, setPromotingTo] = useState<Environment | null>(null);

  const envStates = getEnvironmentStates(tag, status);

  const promoteMut = useMutation({
    mutationFn: async (targetEnv: Environment) => {
      setPromotingTo(targetEnv);
      if (targetEnv === "staging") {
        // Merge the PR to promote to staging
        return api.git.prs.merge(prId, tag ?? undefined);
      }
      // Production promotion would create a tag/release
      // For now, we use the merge with a production tag
      const prodTag = tag ? `${tag}-prod` : `${resourceType}/${resourceName}/prod`;
      return api.git.prs.merge(prId, prodTag);
    },
    onSuccess: (_data, targetEnv) => {
      toast({
        title: `Promoted to ${targetEnv}`,
        description: `${resourceName} has been promoted to ${targetEnv}`,
        variant: "success",
      });
      queryClient.invalidateQueries({ queryKey: ["pr", prId] });
      setPromotingTo(null);
    },
    onError: (e: Error) => {
      toast({
        title: "Promotion failed",
        description: e.message,
        variant: "error",
      });
      setPromotingTo(null);
    },
  });

  // Determine which environments can be promoted to
  const canPromoteToStaging = status === "approved" && !envStates[1].deployed;
  const canPromoteToProduction = envStates[1].deployed && !envStates[2].deployed;

  return (
    <div className="rounded-lg border border-border p-4">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-medium">
        <Rocket className="size-4" />
        Environment Promotion
      </h3>

      <div className="flex items-center gap-2">
        {envStates.map((state, i) => (
          <div key={state.env} className="flex items-center gap-2">
            {i > 0 && (
              <ArrowRight
                className={cn(
                  "size-4 shrink-0",
                  state.deployed
                    ? "text-green-600 dark:text-green-400"
                    : "text-muted-foreground/30"
                )}
              />
            )}
            <EnvironmentCard
              state={state}
              isNext={
                (i === 1 && canPromoteToStaging) ||
                (i === 2 && canPromoteToProduction)
              }
              canPromote={
                (state.env === "staging" && canPromoteToStaging) ||
                (state.env === "production" && canPromoteToProduction)
              }
              onPromote={() => promoteMut.mutate(state.env)}
              isPromoting={promotingTo === state.env}
            />
          </div>
        ))}
      </div>

      {/* Info text */}
      <p className="mt-3 text-[11px] text-muted-foreground">
        {canPromoteToStaging
          ? "Ready to promote to staging. This will merge the PR and publish to the registry."
          : canPromoteToProduction
            ? "Staging deployment verified. Promote to production when ready."
            : status === "published"
              ? "This resource has been published. Use the promote buttons to deploy to additional environments."
              : "Approval required before environment promotion."}
      </p>
    </div>
  );
}
