import { Routes, Route, Navigate } from "react-router-dom";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import LoginPage from "@/pages/Login";
import DashboardPage from "@/pages/Dashboard";
import ConversationsPage from "@/pages/Conversations";
import ContextPage from "@/pages/Context";
import UsersPage from "@/pages/Users";
import ToolsPage from "@/pages/Tools";
import TasksPage from "@/pages/Crons";
import MemoryPage from "@/pages/Memory";
import SettingsPage from "@/pages/Settings";

export default function App() {
  return (
    <Routes>
      <Route path="/dashboard/login" element={<LoginPage />} />
      <Route path="/dashboard" element={<DashboardLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="conversations" element={<ConversationsPage />} />
        <Route path="context" element={<ContextPage />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="tools" element={<ToolsPage />} />
        <Route path="crons" element={<TasksPage />} />
        <Route path="memory" element={<MemoryPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
