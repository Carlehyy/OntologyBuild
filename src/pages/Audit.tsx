import { useEffect, useState } from "react";
import { listAuditLogs } from "@/api/client";
import type { AuditLog } from "@/types";
import { StickyNote, AlertCircle, Loader2 } from "lucide-react";

const actionLabels: Record<string, { color: string }> = {
  create: { color: "text-green-600" },
  update: { color: "text-blue-600" },
  delete: { color: "text-red-600" },
  upload: { color: "text-purple-600" },
  publish: { color: "text-orange-600" },
  inference: { color: "text-cyan-600" },
};

export default function Audit() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadLogs();
  }, []);

  const loadLogs = async () => {
    try { const l = await listAuditLogs(); setLogs(l); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2"><StickyNote className="w-6 h-6" /> 审计日志</h1>
        <p className="text-gray-500 text-sm mt-1">系统中所有重要操作的记录</p>
      </div>

      {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-red-700 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-blue-600" /></div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="px-5 py-3 text-left">时间</th>
                <th className="px-5 py-3 text-left">操作</th>
                <th className="px-5 py-3 text-left">资源</th>
                <th className="px-5 py-3 text-left">资源ID</th>
                <th className="px-5 py-3 text-left">用户</th>
                <th className="px-5 py-3 text-left">详情</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {logs.map((log) => {
                const actionStyle = actionLabels[log.action] || { color: "text-gray-600" };
                return (
                  <tr key={log.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3 text-gray-400 text-xs">{new Date(log.created_at).toLocaleString()}</td>
                    <td className="px-5 py-3"><span className={`font-medium ${actionStyle.color}`}>{log.action}</span></td>
                    <td className="px-5 py-3 text-gray-600">{log.resource_type}</td>
                    <td className="px-5 py-3 text-gray-400 text-xs font-mono">{log.resource_id?.slice(0, 8) || "-"}</td>
                    <td className="px-5 py-3 text-gray-600">{log.user_id || "system"}</td>
                    <td className="px-5 py-3 text-gray-500 text-xs">{log.details ? JSON.stringify(log.details).slice(0, 60) : "-"}</td>
                  </tr>
                );
              })}
              {logs.length === 0 && <tr><td colSpan={6} className="px-5 py-8 text-center text-gray-400">暂无日志</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
