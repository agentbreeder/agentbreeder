import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ListingReview } from "@/lib/api";
import { useState } from "react";
import {
  ArrowLeft,
  Star,
  Download,
  Rocket,
  MessageSquare,
  Sparkles,
} from "lucide-react";
import { useAuth } from "@/hooks/use-auth";

function StarRating({ rating, size = "sm" }: { rating: number; size?: "sm" | "lg" }) {
  const cls = size === "lg" ? "size-5" : "size-3";
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          className={`${cls} ${
            i <= Math.round(rating) ? "fill-amber-400 text-amber-400" : "text-muted-foreground/30"
          }`}
        />
      ))}
    </div>
  );
}

function ReviewCard({ review }: { review: ListingReview }) {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{review.reviewer}</span>
        <StarRating rating={review.rating} />
      </div>
      {review.comment && (
        <p className="mt-2 text-sm text-muted-foreground">{review.comment}</p>
      )}
      <span className="mt-2 block text-xs text-muted-foreground">
        {new Date(review.created_at).toLocaleDateString()}
      </span>
    </div>
  );
}

export default function MarketplaceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewComment, setReviewComment] = useState("");

  const { data: listingRes, isLoading } = useQuery({
    queryKey: ["listing", id],
    queryFn: () => api.marketplace.getListing(id!),
    enabled: !!id,
  });

  const { data: reviewsRes } = useQuery({
    queryKey: ["listing-reviews", id],
    queryFn: () => api.marketplace.getReviews(id!),
    enabled: !!id,
  });

  const installMutation = useMutation({
    mutationFn: () => api.marketplace.install(id!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["listing", id] }),
  });

  const reviewMutation = useMutation({
    mutationFn: () =>
      api.marketplace.addReview(id!, {
        reviewer: user?.email ?? "anonymous",
        rating: reviewRating,
        comment: reviewComment,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing-reviews", id] });
      queryClient.invalidateQueries({ queryKey: ["listing", id] });
      setReviewComment("");
      setReviewRating(5);
    },
  });

  const listing = listingRes?.data;
  const reviews = reviewsRes?.data ?? [];
  const template = listing?.template;

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="mt-4 h-64 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  if (!listing || !template) {
    return (
      <div className="p-6">
        <p className="text-muted-foreground">Listing not found.</p>
      </div>
    );
  }

  return (
    <div className="p-6">
      <Link
        to="/marketplace"
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" /> Back to Marketplace
      </Link>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main info */}
        <div className="lg:col-span-2">
          <div className="rounded-lg border border-border bg-card p-6">
            {listing.featured && (
              <div className="mb-3 inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                <Sparkles className="size-3" /> Featured
              </div>
            )}
            <h1 className="text-2xl font-bold">{template.name}</h1>
            <p className="mt-2 text-muted-foreground">{template.description}</p>

            <div className="mt-4 flex flex-wrap items-center gap-4">
              <StarRating rating={listing.avg_rating} size="lg" />
              <span className="text-sm text-muted-foreground">
                {listing.avg_rating.toFixed(1)} ({listing.review_count} reviews)
              </span>
              <span className="flex items-center gap-1 text-sm text-muted-foreground">
                <Download className="size-4" /> {listing.install_count} installs
              </span>
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <span className="rounded bg-muted px-2 py-1 font-mono text-xs">
                {template.framework}
              </span>
              <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs">
                {template.category.replace("_", " ")}
              </span>
              {template.tags.map((tag) => (
                <span key={tag} className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                  {tag}
                </span>
              ))}
            </div>

            <div className="mt-2 text-xs text-muted-foreground">
              by {template.author} &middot; v{template.version}
            </div>
          </div>

          {/* README */}
          {template.readme && (
            <div className="mt-4 rounded-lg border border-border bg-card p-6">
              <h2 className="mb-3 text-lg font-semibold">README</h2>
              <pre className="whitespace-pre-wrap text-sm text-muted-foreground">
                {template.readme}
              </pre>
            </div>
          )}

          {/* Reviews */}
          <div className="mt-4 rounded-lg border border-border bg-card p-6">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
              <MessageSquare className="size-5" /> Reviews
            </h2>

            {reviews.length === 0 ? (
              <p className="text-sm text-muted-foreground">No reviews yet.</p>
            ) : (
              <div className="space-y-3">
                {reviews.map((r) => (
                  <ReviewCard key={r.id} review={r} />
                ))}
              </div>
            )}

            {/* Add review */}
            <div className="mt-6 border-t border-border pt-4">
              <h3 className="mb-2 text-sm font-medium">Write a Review</h3>
              <div className="mb-2 flex items-center gap-1">
                {[1, 2, 3, 4, 5].map((i) => (
                  <button key={i} onClick={() => setReviewRating(i)}>
                    <Star
                      className={`size-5 ${
                        i <= reviewRating
                          ? "fill-amber-400 text-amber-400"
                          : "text-muted-foreground/30"
                      }`}
                    />
                  </button>
                ))}
              </div>
              <textarea
                value={reviewComment}
                onChange={(e) => setReviewComment(e.target.value)}
                placeholder="Share your experience..."
                rows={3}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
              <button
                onClick={() => reviewMutation.mutate()}
                disabled={reviewMutation.isPending}
                className="mt-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {reviewMutation.isPending ? "Submitting..." : "Submit Review"}
              </button>
            </div>
          </div>
        </div>

        {/* Sidebar: Quick Deploy */}
        <div>
          <div className="sticky top-6 rounded-lg border border-border bg-card p-6">
            <h2 className="mb-4 text-lg font-semibold">Deploy</h2>
            <p className="mb-4 text-sm text-muted-foreground">
              Use this template to create a new agent with one click.
            </p>
            <Link
              to={`/templates/${template.id}`}
              onClick={() => installMutation.mutate()}
              className="mb-3 inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Rocket className="size-4" /> Use Template
            </Link>
            <p className="text-center text-xs text-muted-foreground">
              Customize parameters before deploying
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
