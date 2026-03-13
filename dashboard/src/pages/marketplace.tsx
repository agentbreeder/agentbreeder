import { useQuery } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import { api, type MarketplaceBrowseItem, type TemplateCategory } from "@/lib/api";
import { Star, Download, Search, Sparkles } from "lucide-react";
import { useState } from "react";

const CATEGORY_LABELS: Record<TemplateCategory, string> = {
  customer_support: "Customer Support",
  data_analysis: "Data Analysis",
  code_review: "Code Review",
  research: "Research",
  automation: "Automation",
  content: "Content",
  other: "Other",
};

const SORT_OPTIONS = [
  { value: "rating", label: "Top Rated" },
  { value: "installs", label: "Most Installed" },
  { value: "newest", label: "Newest" },
];

function StarRating({ rating }: { rating: number }) {
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          className={`size-3 ${
            i <= Math.round(rating)
              ? "fill-amber-400 text-amber-400"
              : "text-muted-foreground/30"
          }`}
        />
      ))}
      <span className="ml-1 text-xs text-muted-foreground">
        {rating.toFixed(1)}
      </span>
    </div>
  );
}

function ListingCard({ item }: { item: MarketplaceBrowseItem }) {
  return (
    <Link
      to={`/marketplace/${item.listing_id}`}
      className="group flex flex-col rounded-lg border border-border bg-card p-5 transition-all hover:border-foreground/20 hover:shadow-md"
    >
      {item.featured && (
        <div className="mb-2 inline-flex w-fit items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
          <Sparkles className="size-3" /> Featured
        </div>
      )}

      <h3 className="text-base font-semibold text-foreground group-hover:text-primary">
        {item.name}
      </h3>

      <p className="mt-1 flex-1 text-sm text-muted-foreground line-clamp-2">
        {item.description}
      </p>

      <div className="mt-3 flex items-center gap-2">
        <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
          {item.framework}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px]">
          {CATEGORY_LABELS[item.category]}
        </span>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <StarRating rating={item.avg_rating} />
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Download className="size-3" /> {item.install_count}
          </span>
          <span>{item.review_count} reviews</span>
        </div>
      </div>

      <div className="mt-2 text-[10px] text-muted-foreground">
        by {item.author}
      </div>
    </Link>
  );
}

export default function MarketplacePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const category = searchParams.get("category") ?? undefined;
  const sort = searchParams.get("sort") ?? "rating";
  const [searchQuery, setSearchQuery] = useState(searchParams.get("q") ?? "");

  const { data: response, isLoading } = useQuery({
    queryKey: ["marketplace", page, category, sort, searchParams.get("q")],
    queryFn: () =>
      api.marketplace.browse({
        page,
        category,
        sort,
        q: searchParams.get("q") ?? undefined,
      }),
  });

  const items = response?.data ?? [];
  const total = response?.meta?.total ?? 0;

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const sp = new URLSearchParams(searchParams);
    if (searchQuery.trim()) {
      sp.set("q", searchQuery.trim());
    } else {
      sp.delete("q");
    }
    sp.delete("page");
    setSearchParams(sp);
  };

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Marketplace</h1>
        <p className="text-sm text-muted-foreground">
          Discover and deploy community agent templates
        </p>
      </div>

      {/* Search & Filters */}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <form onSubmit={handleSearch} className="flex-1">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search templates..."
              className="w-full rounded-md border border-border bg-background py-2 pl-10 pr-4 text-sm"
            />
          </div>
        </form>

        <select
          value={sort}
          onChange={(e) => {
            const sp = new URLSearchParams(searchParams);
            sp.set("sort", e.target.value);
            sp.delete("page");
            setSearchParams(sp);
          }}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {/* Category pills */}
      <div className="mb-4 flex flex-wrap gap-2">
        <button
          onClick={() => {
            const sp = new URLSearchParams(searchParams);
            sp.delete("category");
            sp.delete("page");
            setSearchParams(sp);
          }}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            !category
              ? "bg-foreground text-background"
              : "bg-muted text-muted-foreground hover:bg-muted/80"
          }`}
        >
          All
        </button>
        {(Object.entries(CATEGORY_LABELS) as [TemplateCategory, string][]).map(
          ([key, label]) => (
            <button
              key={key}
              onClick={() => {
                const sp = new URLSearchParams(searchParams);
                sp.set("category", key);
                sp.delete("page");
                setSearchParams(sp);
              }}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                category === key
                  ? "bg-foreground text-background"
                  : "bg-muted text-muted-foreground hover:bg-muted/80"
              }`}
            >
              {label}
            </button>
          )
        )}
      </div>

      {/* Results */}
      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-52 animate-pulse rounded-lg border border-border bg-muted" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-16">
          <Search className="size-10 text-muted-foreground/40" />
          <p className="mt-3 text-sm text-muted-foreground">No listings found</p>
          <p className="text-xs text-muted-foreground">
            Try different filters or{" "}
            <Link to="/templates" className="text-primary hover:underline">
              create a template
            </Link>{" "}
            to publish
          </p>
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((item) => (
              <ListingCard key={item.listing_id} item={item} />
            ))}
          </div>
          {total > 20 && (
            <div className="mt-6 flex justify-center gap-2">
              <button
                disabled={page <= 1}
                onClick={() => {
                  const sp = new URLSearchParams(searchParams);
                  sp.set("page", String(page - 1));
                  setSearchParams(sp);
                }}
                className="rounded border border-border px-3 py-1 text-sm disabled:opacity-50"
              >
                Previous
              </button>
              <span className="px-3 py-1 text-sm text-muted-foreground">
                Page {page} of {Math.ceil(total / 20)}
              </span>
              <button
                disabled={page * 20 >= total}
                onClick={() => {
                  const sp = new URLSearchParams(searchParams);
                  sp.set("page", String(page + 1));
                  setSearchParams(sp);
                }}
                className="rounded border border-border px-3 py-1 text-sm disabled:opacity-50"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
