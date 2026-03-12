import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/hooks/use-auth";
import Shell from "@/components/shell";
import LoginPage from "@/pages/login";
import HomePage from "@/pages/home";
import AgentsPage from "@/pages/agents";
import AgentDetailPage from "@/pages/agent-detail";
import AgentBuilderPage from "@/pages/agent-builder";
import ToolsPage from "@/pages/tools";
import ToolDetailPage from "@/pages/tool-detail";
import ModelsPage from "@/pages/models";
import ModelDetailPage from "@/pages/model-detail";
import ModelComparePage from "@/pages/model-compare";
import PromptsPage from "@/pages/prompts";
import PromptDetailPage from "@/pages/prompt-detail";
import PromptBuilderPage from "@/pages/prompt-builder";
import ToolBuilderPage from "@/pages/tool-builder";
import McpServersPage from "@/pages/mcp-servers";
import McpServerDetailPage from "@/pages/mcp-server-detail";
import DeploysPage from "@/pages/deploys";
import ActivityPage from "@/pages/activity";
import SearchPage from "@/pages/search";
import SettingsPage from "@/pages/settings";
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
              <Route path="mcp-servers" element={<McpServersPage />} />
              <Route path="mcp-servers/:id" element={<McpServerDetailPage />} />
              <Route path="deploys" element={<DeploysPage />} />
              <Route path="activity" element={<ActivityPage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
