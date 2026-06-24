import { useEffect, useState } from "react";
import { listUsers } from "@/api/client";
import type { User } from "@/types";
import { Users as UsersIcon, UserCheck, Loader2, AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";

const roleLabels: Record<string, { label: string; color: string }> = {
  admin: { label: "管理员", color: "bg-red-100 text-red-700" },
  domain_expert: { label: "领域专家", color: "bg-blue-100 text-blue-700" },
  rule_maintainer: { label: "规则维护者", color: "bg-purple-100 text-purple-700" },
  reviewer: { label: "审阅者", color: "bg-green-100 text-green-700" },
};

export default function Users() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    try { const u = await listUsers(); setUsers(u); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2"><UsersIcon className="w-6 h-6" /> 用户管理</h1>
        <p className="text-gray-500 text-sm mt-1">RBAC角色权限管理</p>
      </div>

      {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-red-700 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-blue-600" /></div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="px-5 py-3 text-left">用户</th>
                <th className="px-5 py-3 text-left">角色</th>
                <th className="px-5 py-3 text-left">邮箱</th>
                <th className="px-5 py-3 text-left">状态</th>
                <th className="px-5 py-3 text-left">创建时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {users.map((u) => {
                const role = roleLabels[u.role] || { label: u.role, color: "bg-gray-100 text-gray-600" };
                return (
                  <tr key={u.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-gray-100 flex items-center justify-center">
                          <span className="text-sm font-medium text-gray-600">{(u.display_name || u.username)[0].toUpperCase()}</span>
                        </div>
                        <div>
                          <p className="font-medium text-gray-900">{u.display_name || u.username}</p>
                          <p className="text-xs text-gray-400">@{u.username}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3"><Badge className={role.color}>{role.label}</Badge></td>
                    <td className="px-5 py-3 text-gray-600">{u.email}</td>
                    <td className="px-5 py-3">
                      {u.is_active ? (
                        <span className="inline-flex items-center gap-1 text-green-600 text-xs"><UserCheck className="w-3.5 h-3.5" /> 活跃</span>
                      ) : (
                        <span className="text-gray-400 text-xs">已停用</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-gray-400">{new Date(u.created_at).toLocaleDateString()}</td>
                  </tr>
                );
              })}
              {users.length === 0 && <tr><td colSpan={5} className="px-5 py-8 text-center text-gray-400">暂无用户</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
