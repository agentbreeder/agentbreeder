import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/hooks/use-auth";
import { TourProvider } from "@/hooks/use-tour";
import Shell from "@/components/shell";
import { WelcomeTour } from "@/components/welcome-tour";
import LoginPage from "@/pages/login";
import ChangePasswordPage from "@/pages/change-password";
import HomePage from "@/pages/home";
import AgentsPage from "@/pages/agents";
import AgentDetailPage from "@/pages/agent-detail";
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
import SearchPage from "@/pages/search";
import PlaygroundPage from "@/pages/playground";
import SettingsPage from "@/pages/settings";
import SettingsSecretsPage from "@/pages/settings-secrets";
import TeamsPage from "@/pages/teams";
import TeamDetailPage from "@/pages/team-detail";
import TemplatesPage from "@/pages/templates";
import TemplateDetailPage from "@/pages/template-detail";
import MarketplacePage from "@/pages/marketplace";
import MarketplaceDetailPage from "@/pages/marketplace-detail";
import GatewayPage from "@/pages/gateway";
import AgentOpsPage from "@/pages/agentops";
import IncidentsPage from "@/pages/incidents";
import CompliancePage from "@/pages/compliance";
import AgentWizardPage from "@/pages/agent-wizard";
import BuilderInsightsPage from "@/pages/builder-insights";
import { Loader2 } from "lucide-react";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

/** Route guard — redirects to /login if not authenticated.
 *
 * When the authenticated user has ``must_change_password`` (issue #464), every
 * route is intercepted with a redirect to /change-password until the flag
 * clears. The change-password page itself is rendered outside this guard so
 * the user can complete the flow without an infinite redirect loop.
 */
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
  if (user.must_change_password) return <Navigate to="/change-password" replace />;
  return <>{children}</>;
}

/** Route guard for the change-password screen.
 *
 * Requires authentication but bypasses the must_change_password redirect so
 * the user can actually complete the rotation. Once the flag clears, the
 * page itself navigates home.
 */
function RequireAuthOnly({ children }: { children: React.ReactNode }) {
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
              path="/change-password"
              element={
                <RequireAuthOnly>
                  <ChangePasswordPage />
                </RequireAuthOnly>
              }
            />
            <Route
              element={
                <RequireAuth>
                  <TourProvider>
                    <Shell />
                    <WelcomeTour />
                  </TourProvider>
                </RequireAuth>
              }
            >
              <Route index element={<HomePage />} />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="agents/new" element={<AgentWizardPage />} />
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
              <Route path="playground" element={<PlaygroundPage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="settings/secrets" element={<SettingsSecretsPage />} />
              <Route path="teams" element={<TeamsPage />} />
              <Route path="teams/:id" element={<TeamDetailPage />} />
              <Route path="builder-insights" element={<BuilderInsightsPage />} />
              <Route path="templates" element={<TemplatesPage />} />
              <Route path="templates/:id" element={<TemplateDetailPage />} />
              <Route path="marketplace" element={<MarketplacePage />} />
              <Route path="marketplace/:id" element={<MarketplaceDetailPage />} />
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
