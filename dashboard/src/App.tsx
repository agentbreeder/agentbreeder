import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/hooks/use-auth";
import Shell from "@/components/shell";
import LoginPage from "@/pages/login";
import HomePage from "@/pages/home";
import AgentsPage from "@/pages/agents";
import AgentDetailPage from "@/pages/agent-detail";
import AgentBuilderPage from "@/pages/agent-builder";
import AgentRegisterPage from "@/pages/agent-register";
import ToolsPage from "@/pages/tools";
import ToolDetailPage from "@/pages/tool-detail";
import ModelsPage from "@/pages/models";
import ModelDetailPage from "@/pages/model-detail";
import ModelComparePage from "@/pages/model-compare";
import PromptsPage from "@/pages/prompts";
import PromptDetailPage from "@/pages/prompt-detail";
import PromptBuilderPage from "@/pages/prompt-builder";
import ToolBuilderPage from "@/pages/tool-builder";
import A2AAgentsPage from "@/pages/a2a-agents";
import A2AAgentDetailPage from "@/pages/a2a-agent-detail";
import McpServersPage from "@/pages/mcp-servers";
import McpServerDetailPage from "@/pages/mcp-server-detail";
import MemoryBuilderPage from "@/pages/memory-builder";
import RAGBuilderPage from "@/pages/rag-builder";
import DeploysPage from "@/pages/deploys";
import ActivityPage from "@/pages/activity";
import SearchPage from "@/pages/search";
import ApprovalsPage from "@/pages/approvals";
import PRDetailPage from "@/pages/pr-detail";
import PlaygroundPage from "@/pages/playground";
import SettingsPage from "@/pages/settings";
import SettingsSecretsPage from "@/pages/settings-secrets";
import TracesPage from "@/pages/traces";
import TraceDetailPage from "@/pages/trace-detail";
import TeamsPage from "@/pages/teams";
import TeamDetailPage from "@/pages/team-detail";
import CostsPage from "@/pages/costs";
import BudgetsPage from "@/pages/budgets";
import AuditPage from "@/pages/audit";
import LineagePage from "@/pages/lineage";
import OrchestrationBuilderPage from "@/pages/orchestration-builder";
import TemplatesPage from "@/pages/templates";
import TemplateDetailPage from "@/pages/template-detail";
import MarketplacePage from "@/pages/marketplace";
import MarketplaceDetailPage from "@/pages/marketplace-detail";
import EvalDatasetsPage from "@/pages/eval-datasets";
import EvalDatasetDetailPage from "@/pages/eval-dataset-detail";
import EvalRunsPage from "@/pages/eval-runs";
import EvalRunDetailPage from "@/pages/eval-run-detail";
import EvalComparisonPage from "@/pages/eval-comparison";
import GatewayPage from "@/pages/gateway";
import AgentOpsPage from "@/pages/agentops";
import IncidentsPage from "@/pages/incidents";
import CompliancePage from "@/pages/compliance";
import { Loader2 } from "lucide-react";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

/** Route guard — redirects to /login if not authenticated. */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireAuth>
                  <Shell />
                </RequireAuth>
              }
            >
              <Route index element={<HomePage />} />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="agents/builder" element={<AgentBuilderPage />} />
              <Route path="agents/builder/:id" element={<AgentBuilderPage />} />
              <Route path="agents/register" element={<AgentRegisterPage />} />
              <Route path="agents/:id" element={<AgentDetailPage />} />
              <Route path="tools" element={<ToolsPage />} />
              <Route path="tools/builder" element={<ToolBuilderPage />} />
              <Route path="tools/builder/:id" element={<ToolBuilderPage />} />
              <Route path="tools/:id" element={<ToolDetailPage />} />
              <Route path="models" element={<ModelsPage />} />
              <Route path="models/compare" element={<ModelComparePage />} />
              <Route path="models/:id" element={<ModelDetailPage />} />
              <Route path="prompts" element={<PromptsPage />} />
              <Route path="prompts/builder" element={<PromptBuilderPage />} />
              <Route path="prompts/builder/:id" element={<PromptBuilderPage />} />
              <Route path="prompts/:id" element={<PromptDetailPage />} />
              <Route path="a2a" element={<A2AAgentsPage />} />
              <Route path="a2a/:id" element={<A2AAgentDetailPage />} />
              <Route path="mcp-servers" element={<McpServersPage />} />
              <Route path="mcp-servers/:id" element={<McpServerDetailPage />} />
              <Route path="memory" element={<MemoryBuilderPage />} />
              <Route path="rag" element={<RAGBuilderPage />} />
              <Route path="playground" element={<PlaygroundPage />} />
              <Route path="approvals" element={<ApprovalsPage />} />
              <Route path="approvals/:id" element={<PRDetailPage />} />
              <Route path="deploys" element={<DeploysPage />} />
              <Route path="activity" element={<ActivityPage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="settings/secrets" element={<SettingsSecretsPage />} />
              <Route path="traces" element={<TracesPage />} />
              <Route path="traces/:traceId" element={<TraceDetailPage />} />
              <Route path="teams" element={<TeamsPage />} />
              <Route path="teams/:id" element={<TeamDetailPage />} />
              <Route path="costs" element={<CostsPage />} />
              <Route path="budgets" element={<BudgetsPage />} />
              <Route path="audit" element={<AuditPage />} />
              <Route path="lineage" element={<LineagePage />} />
              <Route path="orchestrations/builder" element={<OrchestrationBuilderPage />} />
              <Route path="templates" element={<TemplatesPage />} />
              <Route path="templates/:id" element={<TemplateDetailPage />} />
              <Route path="marketplace" element={<MarketplacePage />} />
              <Route path="marketplace/:id" element={<MarketplaceDetailPage />} />
              <Route path="evals/datasets" element={<EvalDatasetsPage />} />
              <Route path="evals/datasets/:id" element={<EvalDatasetDetailPage />} />
              <Route path="evals/runs" element={<EvalRunsPage />} />
              <Route path="evals/runs/:id" element={<EvalRunDetailPage />} />
              <Route path="evals/compare" element={<EvalComparisonPage />} />
              <Route path="gateway" element={<GatewayPage />} />
              <Route path="agentops" element={<AgentOpsPage />} />
              <Route path="incidents" element={<IncidentsPage />} />
              <Route path="compliance" element={<CompliancePage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
