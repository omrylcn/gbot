import { Menu, Sun, Moon, LogOut } from "lucide-react";
import { useThemeStore } from "@/stores/theme";
import { useAuthStore } from "@/stores/auth";
import { useNavigate } from "react-router-dom";

interface TopbarProps {
  onMenuClick: () => void;
}

export function Topbar({ onMenuClick }: TopbarProps) {
  const { dark, toggle } = useThemeStore();
  const { logout } = useAuthStore();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/dashboard/login");
  };

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between h-14 px-4 bg-card border-b border-border">
      <button
        onClick={onMenuClick}
        className="lg:hidden p-2 rounded-lg hover:bg-sidebar-active text-foreground"
      >
        <Menu className="w-5 h-5" />
      </button>

      <div className="flex-1" />

      <div className="flex items-center gap-2">
        <button
          onClick={toggle}
          className="p-2 rounded-lg hover:bg-sidebar-active text-foreground transition-colors"
          title={dark ? "Light Mode" : "Dark Mode"}
        >
          {dark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
        </button>
        <button
          onClick={handleLogout}
          className="p-2 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 text-muted hover:text-destructive transition-colors"
          title="Logout"
        >
          <LogOut className="w-5 h-5" />
        </button>
      </div>
    </header>
  );
}
