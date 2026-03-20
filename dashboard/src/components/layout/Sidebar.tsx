import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, MessageSquare, Layers, Users,
  Wrench, Clock, Brain, Settings,
} from "lucide-react";
import { useAuthStore } from "@/stores/auth";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Overview" },
  { to: "/dashboard/conversations", icon: MessageSquare, label: "Conversations" },
  { to: "/dashboard/context", icon: Layers, label: "Context" },
  { to: "/dashboard/users", icon: Users, label: "Users" },
  { to: "/dashboard/tools", icon: Wrench, label: "Tools" },
  { to: "/dashboard/crons", icon: Clock, label: "Tasks" },
  { to: "/dashboard/memory", icon: Brain, label: "Memory" },
  { to: "/dashboard/settings", icon: Settings, label: "Settings" },
];

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const { userName } = useAuthStore();

  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={onClose}
        />
      )}
      <aside
        className={cn(
          "fixed top-0 left-0 z-50 h-full w-64 bg-sidebar border-r border-border flex flex-col transition-transform duration-200",
          "lg:translate-x-0 lg:static lg:z-auto",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        {/* Logo */}
        <div className="flex items-center justify-center px-5 py-3 border-b border-border">
          <img src="/logo.svg" alt="GBot" className="w-48 h-auto" />
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/dashboard"}
              onClick={onClose}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors",
                  "text-sidebar-foreground hover:bg-sidebar-active",
                  isActive && "bg-sidebar-active font-medium text-primary"
                )
              }
            >
              <Icon className="w-5 h-5" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* User badge */}
        <div className="px-4 py-3 border-t border-border">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
              <span className="text-sm font-medium text-primary">
                {userName?.charAt(0).toUpperCase() || "?"}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground truncate">{userName || "Admin"}</p>
              <p className="text-xs text-muted">Owner</p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
