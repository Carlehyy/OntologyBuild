import { useEffect, useState } from "react";
import { listDomains, listFeedback, getFeedbackStats } from "@/api/client";
import type { Domain, FeedbackRecord } from "@/types";
import { MessageSquare, ThumbsUp, AlertCircle, Loader2, CheckCircle, XCircle, HelpCircle } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const verdictConfig: Record<string, { icon: any; label: string; color: string }> = {
  useful: { icon: ThumbsUp, label: "有用", color: "text-green-600 bg-green-50" },
  false_positive: { icon: XCircle, label: "误报", color: "text-red-600 bg-red-50" },
  needs_correction: { icon: HelpCircle, label: "需修正", color: "text-amber-600 bg-amber-50" },
  skipped: { icon: CheckCircle, label: "跳过", color: "text-gray-600 bg-gray-50" },
};

export default function Feedback() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [feedbackList, setFeedbackList] = useState<FeedbackRecord[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => { loadDomains(); }, []);
  useEffect(() => { if (selectedDomain) loadFeedback(selectedDomain); }, [selectedDomain]);

  const loadDomains = async () => {
    try { const d = await listDomains(); setDomains(d); if (d.length > 0 && !selectedDomain) setSelectedDomain(d[0].id); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  const loadFeedback = async (domainId: string) => {
    setLoading(true);
    try {
      const [f, s] = await Promise.all([listFeedback(domainId), getFeedbackStats(domainId)]);
      setFeedbackList(f);
      setStats(s);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2"><MessageSquare className="w-6 h-6" /> 反馈中心</h1>
          <p className="text-gray-500 text-sm mt-1">飞轮的燃料 - 所有用户反馈的结构化记录</p>
        </div>
        <Select value={selectedDomain} onValueChange={setSelectedDomain}>
          <SelectTrigger className="w-48"><SelectValue placeholder="选择领域" /></SelectTrigger>
          <SelectContent>{domains.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-red-700 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          {Object.entries(stats.by_verdict || {}).map(([verdict, count]) => {
            const config = verdictConfig[verdict] || verdictConfig.skipped;
            const Icon = config.icon;
            return (
              <div key={verdict} className={`rounded-xl p-4 ${config.color}`}>
                <div className="flex items-center gap-2 mb-1">
                  <Icon className="w-4 h-4" />
                  <span className="text-sm font-medium">{config.label}</span>
                </div>
                <span className="text-2xl font-bold">{count as number}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Feedback List */}
      {loading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-blue-600" /></div>
      ) : feedbackList.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <MessageSquare className="w-12 h-12 mx-auto mb-3 opacity-40" />
          <p>暂无反馈记录</p>
          <p className="text-xs mt-1">在文档抽取和推理问答中提交的反馈将显示在这里</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="px-5 py-3 text-left">类型</th>
                <th className="px-5 py-3 text-left">目标</th>
                <th className="px-5 py-3 text-left">评判</th>
                <th className="px-5 py-3 text-left">时间</th>
                <th className="px-5 py-3 text-left">备注</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {feedbackList.map((f) => {
                const config = verdictConfig[f.verdict] || verdictConfig.skipped;
                const Icon = config.icon;
                return (
                  <tr key={f.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3"><span className="text-gray-600">{f.feedback_type}</span></td>
                    <td className="px-5 py-3"><span className="text-gray-600">{f.target_type}</span></td>
                    <td className="px-5 py-3">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${config.color}`}>
                        <Icon className="w-3 h-3" />{config.label}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-gray-400">{new Date(f.created_at).toLocaleString()}</td>
                    <td className="px-5 py-3 text-gray-500">{f.notes || "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
