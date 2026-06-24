import { useEffect, useState } from "react";
import { getDashboardStats, getSystemConfig, createDomain } from "@/api/client";
import type { DashboardStats, SystemConfig } from "@/types";
import {
  Boxes, Shield, GitBranch, FileText, MessageSquare,
  AlertCircle, CheckCircle, Loader2, Brain, Plus,
  TrendingUp, Zap, ArrowRight
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreateDomain, setShowCreateDomain] = useState(false);
  const [newDomain, setNewDomain] = useState({ name: "", description: "" });
  const [creating, setCreating] = useState(false);

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [s, c] = await Promise.all([
        getDashboardStats().catch(() => null),
        getSystemConfig().catch(() => null),
      ]);
      setStats(s);
      setConfig(c);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  const handleCreateDomain = async () => {
    if (!newDomain.name.trim()) return;
    setCreating(true);
    try {
      await createDomain(newDomain);
      setNewDomain({ name: "", description: "" });
      setShowCreateDomain(false);
      loadData();
    } catch (e: any) { alert(e.message); }
    finally { setCreating(false); }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  const statCards = [
    { label: "领域", value: stats?.total_domains ?? 0, icon: Boxes, color: "text-blue-600", bg: "from-blue-50 to-blue-100/50", border: "border-blue-200" },
    { label: "实体", value: stats?.total_entities ?? 0, icon: GitBranch, color: "text-emerald-600", bg: "from-emerald-50 to-emerald-100/50", border: "border-emerald-200" },
    { label: "关系", value: stats?.total_relations ?? 0, icon: TrendingUp, color: "text-purple-600", bg: "from-purple-50 to-purple-100/50", border: "border-purple-200" },
    { label: "规则", value: stats?.domain_stats?.reduce((a, d) => a + d.rules_count, 0) ?? 0, icon: Shield, color: "text-orange-600", bg: "from-orange-50 to-orange-100/50", border: "border-orange-200" },
    { label: "文档", value: stats?.total_documents ?? 0, icon: FileText, color: "text-cyan-600", bg: "from-cyan-50 to-cyan-100/50", border: "border-cyan-200" },
    { label: "待审", value: stats?.pending_reviews ?? 0, icon: MessageSquare, color: "text-red-600", bg: "from-red-50 to-red-100/50", border: "border-red-200" },
  ];

  return (
    <div className="p-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-slate-900 tracking-tight">仪表盘</h1>
          <p className="text-slate-500 text-sm mt-1">
            本体-图谱-AI 自进化框架 · 阶段一：执行 + 反馈记录
          </p>
        </div>
        <div className="flex items-center gap-3">
          {config && (
            <div className="flex items-center gap-2 px-3 py-1.5 bg-white rounded-lg border border-slate-200 text-xs">
              <Brain className="w-3.5 h-3.5 text-purple-500" />
              <span className="text-slate-500">LLM:</span>
              {config.llm_available ? (
                <span className="flex items-center gap-1 text-green-600 font-medium">
                  <CheckCircle className="w-3 h-3" /> {config.llm_model}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-amber-600">
                  <AlertCircle className="w-3 h-3" /> 兜底模式
                </span>
              )}
            </div>
          )}
          <Dialog open={showCreateDomain} onOpenChange={setShowCreateDomain}>
            <DialogTrigger asChild>
              <Button size="sm" className="bg-blue-600 hover:bg-blue-700">
                <Plus className="w-4 h-4 mr-1" /> 创建领域
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader><DialogTitle>创建新领域</DialogTitle></DialogHeader>
              <div className="space-y-3 mt-2">
                <div><Label>领域名称 *</Label>
                  <Input value={newDomain.name} onChange={e => setNewDomain({ ...newDomain, name: e.target.value })} placeholder="如：IT服务管理" />
                </div>
                <div><Label>描述</Label>
                  <Textarea value={newDomain.description} onChange={e => setNewDomain({ ...newDomain, description: e.target.value })} placeholder="描述该领域的业务范围" />
                </div>
                <Button onClick={handleCreateDomain} disabled={creating || !newDomain.name.trim()} className="w-full">
                  {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : "创建"}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {error && (
        <div className="mb-6 bg-red-50 border border-red-200 rounded-xl p-4 flex items-center gap-3 text-red-700 text-sm">
          <AlertCircle className="w-5 h-5 flex-shrink-0" />
          <div><p className="font-medium">连接错误</p><p>{error}</p></div>
        </div>
      )}

      {/* Stat Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
        {statCards.map(card => (
          <div key={card.label} className={`bg-gradient-to-br ${card.bg} border ${card.border} rounded-xl p-4 transition-all hover:shadow-md`}>
            <div className="flex items-center justify-between mb-3">
              <card.icon className={`w-5 h-5 ${card.color}`} />
              <span className={`text-2xl font-bold ${card.color}`}>{card.value}</span>
            </div>
            <p className="text-slate-500 text-xs font-medium">{card.label}</p>
          </div>
        ))}
      </div>

      {/* Domain Details */}
      {stats?.domain_stats && stats.domain_stats.length > 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm mb-8">
          <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900">领域详情</h2>
            <span className="text-xs text-slate-400">{stats.domain_stats.length} 个领域</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-6 py-3 text-left font-medium">领域</th>
                  <th className="px-6 py-3 text-center font-medium">对象类型</th>
                  <th className="px-6 py-3 text-center font-medium">关系类型</th>
                  <th className="px-6 py-3 text-center font-medium">规则</th>
                  <th className="px-6 py-3 text-center font-medium">实体</th>
                  <th className="px-6 py-3 text-center font-medium">关系</th>
                  <th className="px-6 py-3 text-center font-medium">待审</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {stats.domain_stats.map(ds => (
                  <tr key={ds.domain_id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-3 font-medium text-slate-900">{ds.domain_name}</td>
                    <td className="px-6 py-3 text-center text-slate-600">{ds.object_types_count}</td>
                    <td className="px-6 py-3 text-center text-slate-600">{ds.relation_types_count}</td>
                    <td className="px-6 py-3 text-center">
                      <span className="text-green-600 font-medium">{ds.rules_active_count}</span>
                      <span className="text-slate-400 mx-1">/</span>
                      <span className="text-slate-600">{ds.rules_count}</span>
                    </td>
                    <td className="px-6 py-3 text-center text-slate-600">{ds.entities_count}</td>
                    <td className="px-6 py-3 text-center text-slate-600">{ds.relations_count}</td>
                    <td className="px-6 py-3 text-center">
                      {ds.documents_pending_review > 0 ? (
                        <span className="inline-flex items-center gap-1 text-red-600 font-medium bg-red-50 px-2 py-0.5 rounded-full text-xs">
                          {ds.documents_pending_review}
                        </span>
                      ) : <span className="text-slate-300">-</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 border-dashed p-12 text-center mb-8">
          <Boxes className="w-12 h-12 mx-auto mb-3 text-slate-300" />
          <h3 className="text-lg font-medium text-slate-700 mb-1">还没有领域</h3>
          <p className="text-slate-400 text-sm mb-4">点击上方"创建领域"按钮开始</p>
          <Button size="sm" onClick={() => setShowCreateDomain(true)}>
            <Plus className="w-4 h-4 mr-1" /> 创建第一个领域
          </Button>
        </div>
      )}

      {/* Quick Actions */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {[
          { title: "文档抽取", desc: "上传文档并运行LLM抽取，生成候选本体和实体。", link: "/extraction", icon: FileText, color: "bg-blue-50 text-blue-600" },
          { title: "图谱浏览", desc: "可视化浏览和查询知识图谱中的实体与关系。", link: "/graph", icon: GitBranch, color: "bg-purple-50 text-purple-600" },
          { title: "推理问答", desc: "使用规则+LLM对图谱进行智能问答和推理。", link: "/inference", icon: Zap, color: "bg-amber-50 text-amber-600" },
        ].map(item => (
          <div key={item.link} className="bg-white rounded-xl border border-slate-200 p-6 hover:shadow-lg hover:border-blue-300 transition-all group cursor-pointer"
            onClick={() => window.location.hash = item.link}>
            <div className={`w-10 h-10 rounded-lg ${item.color} flex items-center justify-center mb-4`}>
              <item.icon className="w-5 h-5" />
            </div>
            <h3 className="font-semibold text-slate-900 mb-1">{item.title}</h3>
            <p className="text-slate-500 text-sm mb-4">{item.desc}</p>
            <span className="text-blue-600 text-sm font-medium flex items-center gap-1 group-hover:gap-2 transition-all">
              前往 <ArrowRight className="w-4 h-4" />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
