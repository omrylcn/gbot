import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { adminApi } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { RoleBadge } from "@/components/shared/Badge";
import { formatDate } from "@/lib/utils";

const ROLES = ["owner", "member", "guest"];

export default function UsersPage() {
  const queryClient = useQueryClient();

  const { data: users, isLoading } = useQuery({
    queryKey: ["users"],
    queryFn: adminApi.getUsers,
  });

  const roleMut = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      adminApi.setUserRole(userId, role),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });

  if (isLoading) return <LoadingSpinner />;

  return (
    <div>
      <PageHeader title="Users" description="User list and role management" />

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-background/50">
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">User</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">Name</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">Role</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">Registered</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users?.map((u) => (
              <tr key={u.user_id} className="border-b border-border last:border-0 hover:bg-sidebar-active transition-colors">
                <td className="px-5 py-3">
                  <span className="text-sm font-medium text-foreground">@{u.user_id}</span>
                </td>
                <td className="px-5 py-3 text-sm text-foreground">{u.name || "—"}</td>
                <td className="px-5 py-3"><RoleBadge role={u.role || "guest"} /></td>
                <td className="px-5 py-3 text-sm text-muted">{formatDate(u.created_at)}</td>
                <td className="px-5 py-3">
                  <select
                    value={u.role || "guest"}
                    onChange={(e) => roleMut.mutate({ userId: u.user_id, role: e.target.value })}
                    className="px-2 py-1 text-sm bg-background border border-border rounded-lg text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
