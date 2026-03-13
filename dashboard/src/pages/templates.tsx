import { useQuery } from "@tanstack/react-query";
import { useSearchParams, Link } from "react-router-dom";
import { api, type Template, type TemplateCategory } from "@/lib/api";
import { Plus, Package, ArrowUpDown } from "lucide-react";

const CATEGORY_LABELS: Record<TemplateCategory, string> = {
  customer_support: "Customer Support",
  data_analysis: "Data Analysis",
  code_review: "Code Review",
  research: "Research",
  automation: "Automation",
  content: "Content",
  other: "Other",
};

const CATEGORY_COLORS: Record<TemplateCategory, string> = {
  customer_support: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  data_analysis: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  code_review: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  research: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  automation: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400",
  content: "bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-400",
  other: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400",
};

function TemplateCard({ template }: { template: Template }) {
  return (
    <Link
      to={`/templates/${template.id}`}
      className="group rounded-lg border border-border bg-card p-5 transition-all hover:border-foreground/20 hover:shadow-md"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <Package className="size-5 text-muted-foreground" />
          <h3 className="font-semibold text-foreground group-hover:text-primary">
            {template.name}
          </h3>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${CATEGORY_COLORS[template.category]}`}
        >
          {CATEGORY_LABELS[template.category]}
        </span>
      </div>

      <p className="mt-2 text-sm text-muted-foreground line-clamp-2">
        {template.description || "No description"}
      </p>

      <div className="mt-4 flex items-center gap-3 text-xs text-muted-foreground">
        <span className="rounded bg-muted px-1.5 py-0.5 font-mono">{template.framework}</span>
        <span>v{template.version}</span>
        <span className="flex items-center gap-1">
          <ArrowUpDown className="size-3" />
          {template.use_count} uses
        </span>
      </div>

      <div className="mt-3 flex flex-wrap gap-1">
        {template.tags.slice(0, 4).map((tag) => (
          <span
            key={tag}
            className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
          >
            {tag}
          </span>
        ))}
      </div>
    </Link>
  );
}

export default function TemplatesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = parseInt(searchParams.get("page") ?? "1", 10);
  const category = searchParams.get("category") ?? undefined;
  const framework = searchParams.get("framework") ?? undefined;

  const { data: response, isLoading } = useQuery({
    queryKey: ["templates", page, category, framework],
    queryFn: () => api.templates.list({ page, category, framework }),
  });

  const templates = response?.data ?? [];
  const total = response?.meta?.total ?? 0;

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Templates</h1>
          <p className="text-sm text-muted-foreground">
            Parameterized agent configurations for quick deployment
          </p>
        </div>
        <Link
          to="/templates/new"
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          <Plus className="size-4" /> New Template
        </Link>
      </div>

      {/* Category filters */}
      <div className="mb-4 flex flex-wrap gap-2">
        <button
          onClick={() => {
            const sp = new URLSearchParams(searchParams);
            sp.delete("category");
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
        {(Object.entries(CATEGORY_LABELS) as [TemplateCategory, string][]).map(([key, label]) => (
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
        ))}
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-48 animate-pulse rounded-lg border border-border bg-muted" />
          ))}
        </div>
      ) : templates.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border py-16">
          <Package className="size-10 text-muted-foreground/40" />
          <p className="mt-3 text-sm text-muted-foreground">No templates found</p>
          <Link
            to="/templates/new"
            className="mt-3 text-sm text-primary hover:underline"
          >
            Create your first template
          </Link>
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((t) => (
              <TemplateCard key={t.id} template={t} />
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
