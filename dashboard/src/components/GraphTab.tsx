import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GraphEntity, type GraphRelationship } from "@/lib/api";
import { Loader2 } from "lucide-react";

interface GraphTabProps {
  indexId: string;
}

const ENTITY_TYPE_COLORS: Record<string, string> = {
  concept: "#22c55e",
  organization: "#a855f7",
  person: "#3b82f6",
  location: "#f59e0b",
  event: "#ef4444",
  other: "#6b7280",
};

function entityColor(type: string): string {
  return ENTITY_TYPE_COLORS[type] ?? ENTITY_TYPE_COLORS.other;
}

function EntityTypeBadge({ type }: { type: string }) {
  const color = entityColor(type);
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase"
      style={{ background: `${color}22`, color }}
    >
      {type}
    </span>
  );
}

interface EgoGraphProps {
  center: GraphEntity;
  neighbors: { entity: GraphEntity; predicate: string }[];
  onSelectEntity: (entity: GraphEntity) => void;
}

function EgoGraph({ center, neighbors, onSelectEntity }: EgoGraphProps) {
  const W = 360;
  const H = 280;
  const cx = W / 2;
  const cy = H / 2;
  const radius = 100;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${W} ${H}`}
      className="select-none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {neighbors.map((n, i) => {
        const angle = (2 * Math.PI * i) / Math.max(neighbors.length, 1) - Math.PI / 2;
        const nx = cx + radius * Math.cos(angle);
        const ny = cy + radius * Math.sin(angle);
        const color = entityColor(n.entity.entity_type);
        const midX = (cx + nx) / 2;
        const midY = (cy + ny) / 2;
        return (
          <g key={n.entity.id}>
            <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="#374151" strokeWidth="1" />
            <text
              x={midX}
              y={midY - 3}
              textAnchor="middle"
              fill="#6b7280"
              fontSize="8"
              fontFamily="monospace"
            >
              {n.predicate.length > 12 ? n.predicate.slice(0, 12) + "…" : n.predicate}
            </text>
            <circle
              cx={nx}
              cy={ny}
              r="18"
              fill="#1c1c1e"
              stroke={color}
              strokeWidth="1.5"
              className="cursor-pointer hover:opacity-80"
              onClick={() => onSelectEntity(n.entity)}
            />
            <text
              x={nx}
              y={ny + 3}
              textAnchor="middle"
              fill={color}
              fontSize="7"
              fontFamily="monospace"
            >
              {n.entity.entity.length > 9 ? n.entity.entity.slice(0, 9) + "…" : n.entity.entity}
            </text>
          </g>
        );
      })}
      <circle cx={cx} cy={cy} r="22" fill="#1c1c1e" stroke="white" strokeWidth="2" />
      <text x={cx} y={cy + 4} textAnchor="middle" fill="white" fontSize="7" fontFamily="monospace">
        {center.entity.length > 11 ? center.entity.slice(0, 11) + "…" : center.entity}
      </text>
    </svg>
  );
}

export function GraphTab({ indexId }: GraphTabProps) {
  const [page, setPage] = useState(1);
  const [entityTypeFilter, setEntityTypeFilter] = useState<string>("");
  const [selectedEntity, setSelectedEntity] = useState<GraphEntity | null>(null);

  const PER_PAGE = 20;

  const { data: metaResp, isLoading: metaLoading } = useQuery({
    queryKey: ["graph-meta", indexId],
    queryFn: () => api.rag.getGraphMeta(indexId),
  });

  const { data: entitiesResp, isLoading: entitiesLoading } = useQuery({
    queryKey: ["graph-entities", indexId, page, entityTypeFilter],
    queryFn: () =>
      api.rag.listEntities(indexId, {
        page,
        per_page: PER_PAGE,
        entity_type: entityTypeFilter || undefined,
      }),
  });

  const { data: relsResp } = useQuery({
    queryKey: ["graph-rels", indexId],
    queryFn: () => api.rag.listRelationships(indexId, { per_page: 200 }),
    enabled: !!selectedEntity,
  });

  const meta = metaResp?.data;
  const entities = entitiesResp?.data ?? [];
  const totalEntities = entitiesResp?.meta?.total ?? 0;
  const totalPages = Math.ceil(totalEntities / PER_PAGE);
  const allRels: GraphRelationship[] = relsResp?.data ?? [];

  const neighbors: { entity: GraphEntity; predicate: string }[] = [];
  if (selectedEntity) {
    const neighborIds = new Set<string>();
    const neighborPredicates = new Map<string, string>();
    for (const rel of allRels) {
      if (rel.subject_id === selectedEntity.id && !neighborIds.has(rel.object_id)) {
        neighborIds.add(rel.object_id);
        neighborPredicates.set(rel.object_id, rel.predicate);
      } else if (rel.object_id === selectedEntity.id && !neighborIds.has(rel.subject_id)) {
        neighborIds.add(rel.subject_id);
        neighborPredicates.set(rel.subject_id, rel.predicate);
      }
    }
    for (const ent of entities) {
      if (neighborIds.has(ent.id)) {
        neighbors.push({ entity: ent, predicate: neighborPredicates.get(ent.id) ?? "related" });
      }
    }
  }

  const entityTypes = meta?.entity_types?.map((et) => et.type) ?? [];

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      {metaLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" /> Loading graph stats...
        </div>
      ) : meta ? (
        <div className="flex items-center gap-4 rounded-md border border-border bg-muted/30 px-4 py-2">
          <div className="text-center">
            <p className="text-xs text-muted-foreground">Entities</p>
            <p className="text-lg font-semibold">{meta.node_count}</p>
          </div>
          <div className="h-8 w-px bg-border" />
          <div className="text-center">
            <p className="text-xs text-muted-foreground">Relationships</p>
            <p className="text-lg font-semibold">{meta.edge_count}</p>
          </div>
          <div className="h-8 w-px bg-border" />
          <div className="flex flex-wrap gap-1">
            {meta.entity_types.map((et) => (
              <EntityTypeBadge key={et.type} type={et.type} />
            ))}
          </div>
        </div>
      ) : null}

      <div className="grid flex-1 grid-cols-[280px_1fr] gap-4 overflow-hidden">
        <div className="flex flex-col overflow-hidden rounded-md border border-border">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <span className="flex-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Entities
            </span>
            {entityTypes.length > 0 && (
              <select
                data-testid="entity-type-filter"
                className="rounded border border-border bg-background px-1.5 py-0.5 text-xs text-foreground"
                value={entityTypeFilter}
                onChange={(e) => {
                  setEntityTypeFilter(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">All types</option>
                {entityTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div className="flex-1 overflow-y-auto">
            {entitiesLoading ? (
              <div className="flex h-20 items-center justify-center">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            ) : entities.length === 0 ? (
              <p className="p-4 text-xs text-muted-foreground">No entities found.</p>
            ) : (
              entities.map((ent) => (
                <button
                  key={ent.id}
                  className={`w-full border-b border-border px-3 py-2 text-left transition-colors hover:bg-muted/50 ${
                    selectedEntity?.id === ent.id ? "bg-muted" : ""
                  }`}
                  onClick={() => setSelectedEntity(ent)}
                >
                  <div className="flex items-center gap-2">
                    <span className="truncate text-xs font-medium">{ent.entity}</span>
                    <EntityTypeBadge type={ent.entity_type} />
                  </div>
                  {ent.description && (
                    <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                      {ent.description}
                    </p>
                  )}
                </button>
              ))
            )}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-border px-3 py-1.5">
              <button
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                ← Prev
              </button>
              <span className="text-[10px] text-muted-foreground">
                {page} / {totalPages}
              </span>
              <button
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </div>

        <div className="flex flex-col overflow-hidden rounded-md border border-border">
          {selectedEntity ? (
            <>
              <div className="border-b border-border px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">{selectedEntity.entity}</span>
                  <EntityTypeBadge type={selectedEntity.entity_type} />
                </div>
                {selectedEntity.description && (
                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                    {selectedEntity.description}
                  </p>
                )}
              </div>
              <div className="flex flex-1 items-center justify-center p-4">
                {neighbors.length === 0 ? (
                  <div className="text-center">
                    <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full border-2 border-white/30">
                      <span className="text-xs font-mono text-white/60">
                        {selectedEntity.entity.slice(0, 3)}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">No connected entities</p>
                  </div>
                ) : (
                  <EgoGraph
                    center={selectedEntity}
                    neighbors={neighbors}
                    onSelectEntity={(ent) => setSelectedEntity(ent)}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center">
              <p className="text-xs text-muted-foreground">
                Click an entity to explore its connections
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
