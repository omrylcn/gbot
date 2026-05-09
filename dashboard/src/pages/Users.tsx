import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Pencil, Trash2, X } from "lucide-react";

import { adminApi, type User } from "@/api/admin";
import { PageHeader } from "@/components/shared/PageHeader";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { useAuthStore } from "@/stores/auth";
import { formatDate } from "@/lib/utils";

const ROLES = ["owner", "member", "guest"];

export default function UsersPage() {
  const queryClient = useQueryClient();
  const { userId: currentUserId } = useAuthStore();

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<User | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: users, isLoading } = useQuery({
    queryKey: ["users"],
    queryFn: adminApi.getUsers,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["users"] });

  const roleMut = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      adminApi.setUserRole(userId, role),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  if (isLoading) return <LoadingSpinner />;

  return (
    <div>
      <PageHeader title="Users" description="User list and role management">
        <button
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-2 px-3 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90"
        >
          <Plus className="w-4 h-4" /> New User
        </button>
      </PageHeader>

      {error && (
        <div className="mb-4 px-4 py-3 bg-red-500/10 border border-red-500/30 text-red-500 rounded-lg text-sm flex justify-between items-center">
          <span>{error}</span>
          <button onClick={() => setError(null)}>
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-background/50">
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">User</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">Name</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">Role</th>
              <th className="px-5 py-3 text-left text-xs font-medium text-muted uppercase">Registered</th>
              <th className="px-5 py-3 text-right text-xs font-medium text-muted uppercase">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users?.map((u) => {
              const isSelf = u.user_id === currentUserId;
              const isOwner = (u.role || "guest") === "owner";
              return (
                <tr key={u.user_id} className="border-b border-border last:border-0 hover:bg-sidebar-active transition-colors">
                  <td className="px-5 py-3">
                    <span className="text-sm font-medium text-foreground">@{u.user_id}</span>
                    {isSelf && <span className="ml-2 text-xs text-muted">(you)</span>}
                  </td>
                  <td className="px-5 py-3 text-sm text-foreground">{u.name || "—"}</td>
                  <td className="px-5 py-3">
                    <select
                      value={u.role || "guest"}
                      onChange={(e) => roleMut.mutate({ userId: u.user_id, role: e.target.value })}
                      disabled={isSelf}
                      className="px-2 py-1 text-sm bg-background border border-border rounded-lg text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                  </td>
                  <td className="px-5 py-3 text-sm text-muted">{formatDate(u.created_at)}</td>
                  <td className="px-5 py-3">
                    <div className="flex justify-end gap-1">
                      <button
                        onClick={() => setEditTarget(u)}
                        className="p-1.5 text-muted hover:text-foreground hover:bg-sidebar-active rounded-md transition-colors"
                        title="Edit user"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setDeleteTarget(u)}
                        disabled={isSelf || isOwner}
                        className="p-1.5 text-muted hover:text-red-500 hover:bg-sidebar-active rounded-md transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                        title={isSelf ? "Cannot delete yourself" : isOwner ? "Cannot delete owner" : "Delete user"}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {createOpen && (
        <CreateUserModal
          onClose={() => setCreateOpen(false)}
          onSuccess={() => {
            invalidate();
            setCreateOpen(false);
          }}
          onError={setError}
        />
      )}

      {editTarget && (
        <EditUserModal
          user={editTarget}
          onClose={() => setEditTarget(null)}
          onSuccess={() => {
            invalidate();
            setEditTarget(null);
          }}
          onError={setError}
        />
      )}

      {deleteTarget && (
        <DeleteUserModal
          user={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onSuccess={() => {
            invalidate();
            setDeleteTarget(null);
          }}
          onError={setError}
        />
      )}
    </div>
  );
}

interface ModalProps {
  onClose: () => void;
  onSuccess: () => void;
  onError: (msg: string) => void;
}

function CreateUserModal({ onClose, onSuccess, onError }: ModalProps) {
  const [userId, setUserId] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("member");
  const [password, setPassword] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      adminApi.createUser({
        user_id: userId.trim(),
        name: name.trim() || undefined,
        role,
        password: password || undefined,
      }),
    onSuccess,
    onError: (e: Error) => onError(e.message),
  });

  return (
    <ModalShell title="Create User" onClose={onClose}>
      <div className="space-y-4">
        <Field label="User ID" required>
          <input
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            placeholder="e.g. alice"
            className={inputCls}
            autoFocus
          />
        </Field>
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Display name (optional)"
            className={inputCls}
          />
        </Field>
        <Field label="Role">
          <select value={role} onChange={(e) => setRole(e.target.value)} className={inputCls}>
            {ROLES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </Field>
        <Field label="Password">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Leave blank for no password"
            className={inputCls}
          />
        </Field>
      </div>
      <ModalFooter
        onClose={onClose}
        onConfirm={() => mut.mutate()}
        confirmLabel="Create"
        confirmDisabled={!userId.trim() || mut.isPending}
      />
    </ModalShell>
  );
}

function EditUserModal({ user, onClose, onSuccess, onError }: ModalProps & { user: User }) {
  const [name, setName] = useState(user.name || "");
  const [password, setPassword] = useState("");

  const mut = useMutation({
    mutationFn: () => {
      const payload: { name?: string; password?: string } = {};
      if (name !== (user.name || "")) payload.name = name;
      if (password) payload.password = password;
      return adminApi.updateUser(user.user_id, payload);
    },
    onSuccess,
    onError: (e: Error) => onError(e.message),
  });

  const dirty = name !== (user.name || "") || password.length > 0;

  return (
    <ModalShell title={`Edit @${user.user_id}`} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputCls}
            autoFocus
          />
        </Field>
        <Field label="New Password">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Leave blank to keep current password"
            className={inputCls}
          />
        </Field>
        <p className="text-xs text-muted">
          Role changes use the table dropdown.
        </p>
      </div>
      <ModalFooter
        onClose={onClose}
        onConfirm={() => mut.mutate()}
        confirmLabel="Save"
        confirmDisabled={!dirty || mut.isPending}
      />
    </ModalShell>
  );
}

function DeleteUserModal({ user, onClose, onSuccess, onError }: ModalProps & { user: User }) {
  const [confirmText, setConfirmText] = useState("");

  const mut = useMutation({
    mutationFn: () => adminApi.deleteUser(user.user_id),
    onSuccess,
    onError: (e: Error) => onError(e.message),
  });

  const matches = confirmText === user.user_id;

  return (
    <ModalShell title="Delete User" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-foreground">
          Permanently delete <span className="font-medium">@{user.user_id}</span>?
        </p>
        <div className="px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-xs text-red-400">
          This cascades through every per-user table: facts, relations, entity pages, sessions,
          messages, tasks, channels, notes, API keys. Cannot be undone.
        </div>
        <Field label={`Type "${user.user_id}" to confirm`}>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            className={inputCls}
            autoFocus
          />
        </Field>
      </div>
      <ModalFooter
        onClose={onClose}
        onConfirm={() => mut.mutate()}
        confirmLabel="Delete"
        confirmDisabled={!matches || mut.isPending}
        confirmDanger
      />
    </ModalShell>
  );
}

const inputCls =
  "w-full px-3 py-2 bg-background border border-border rounded-lg text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50";

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-muted mb-1">
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
      </span>
      {children}
    </label>
  );
}

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-card border border-border rounded-2xl w-full max-w-md flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h3 className="font-medium text-foreground">{title}</h3>
          <button onClick={onClose} className="text-muted hover:text-foreground">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function ModalFooter({
  onClose,
  onConfirm,
  confirmLabel,
  confirmDisabled,
  confirmDanger,
}: {
  onClose: () => void;
  onConfirm: () => void;
  confirmLabel: string;
  confirmDisabled?: boolean;
  confirmDanger?: boolean;
}) {
  return (
    <div className="flex justify-end gap-2 mt-6 pt-4 border-t border-border -mx-5 px-5">
      <button
        onClick={onClose}
        className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-sidebar-active"
      >
        Cancel
      </button>
      <button
        onClick={onConfirm}
        disabled={confirmDisabled}
        className={
          confirmDanger
            ? "px-4 py-2 text-sm bg-red-500 text-white rounded-lg hover:bg-red-500/90 disabled:opacity-40 disabled:cursor-not-allowed"
            : "px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
        }
      >
        {confirmLabel}
      </button>
    </div>
  );
}
