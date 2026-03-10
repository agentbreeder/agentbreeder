import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Shell from "@/components/shell";
import HomePage from "@/pages/home";
import AgentsPage from "@/pages/agents";
import AgentDetailPage from "@/pages/agent-detail";
import ToolsPage from "@/pages/tools";
import SearchPage from "@/pages/search";
import PlaceholderPage from "@/pages/placeholder";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<HomePage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="agents/:id" element={<AgentDetailPage />} />
            <Route path="tools" element={<ToolsPage />} />
            <Route path="models" element={<PlaceholderPage />} />
            <Route path="prompts" element={<PlaceholderPage />} />
            <Route path="deploys" element={<PlaceholderPage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
