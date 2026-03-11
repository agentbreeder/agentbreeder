import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/hooks/use-auth";
import Shell from "@/components/shell";
import LoginPage from "@/pages/login";
import HomePage from "@/pages/home";
import AgentsPage from "@/pages/agents";
import AgentDetailPage from "@/pages/agent-detail";
import ToolsPage from "@/pages/tools";
import ModelsPage from "@/pages/models";
import PromptsPage from "@/pages/prompts";
import PromptDetailPage from "@/pages/prompt-detail";
import DeploysPage from "@/pages/deploys";
import SearchPage from "@/pages/search";
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
              <Route path="agents/:id" element={<AgentDetailPage />} />
              <Route path="tools" element={<ToolsPage />} />
              <Route path="models" element={<ModelsPage />} />
              <Route path="prompts" element={<PromptsPage />} />
              <Route path="prompts/:id" element={<PromptDetailPage />} />
              <Route path="deploys" element={<DeploysPage />} />
              <Route path="search" element={<SearchPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
