import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface StatCardProps {
  title: string;
  value: string | number;
  icon: LucideIcon;
  description?: string;
  className?: string;
}

export function StatCard({ title, value, icon: Icon, description, className }: StatCardProps) {
  return (
    <div className={cn("bg-card border border-border rounded-xl p-5", className)}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-muted">{title}</span>
        <Icon className="w-5 h-5 text-muted" />
      </div>
      <p className="text-2xl font-bold text-card-foreground">{value}</p>
      {description && (
        <p className="text-xs text-muted mt-1">{description}</p>
      )}
    </div>
  );
}
