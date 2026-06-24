import { useEffect, useState } from "react";
import { listDomains, listRules, createRule, toggleRule, publishRule, deleteRule } from "@/api/client";
import type { Domain, Rule } from "@/types";
import { Plus, Shield, Play, Pause, Trash2, AlertCircle, Loader2, Send, History, XCircle, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

// Mock execution history for demo
const mockExecutions: Record<string, any[]> = {
  "any": [
    { id: "ex1", entity_name: "INC-2024-001", entity_type: "Incident", action_type: "emit_alert", severity: "critical", message: "P1 Critical Incident requires immediate attention", status: "triggered", created_at: "2026-06-24T10:30:00" },
    { id: "ex2", entity_name: "CHG-2024-015", entity_type: "Change", action_type: "require_approval", severity: "high", message: "High-risk change requires CAB approval", status: "dismissed", created_at: "2026-06-24T09:15:00", dismissed_by: "admin", dismissed_reason: "Approved in emergency CAB meeting" },
    { id: "ex3", entity_name: "INC-2024-002", entity_type: "Incident", action_type: "emit_alert", severity: "medium", message: "Incident has been unassigned for too long", status: "triggered", created_at: "2026-06-24T08:00:00" },
  ]
};

export default function Rules() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [expandedRule, setExpandedRule] = useState<string | null>(null);
  const [newRule, setNewRule] = useState<Record<string, any>>({
    name: "", description: "",
    condition: { pattern: "entity_match", parameters: { entity_type: "", prop_status: "" } },
    action_type: "emit_alert",
    action_config: { message_template: "", severity: "medium" },
    priority: 0,
  });

  useEffect(() => { loadDomains(); }, []);
  useEffect(() => { if (selectedDomain) loadRules(selectedDomain); }, [selectedDomain]);

  const loadDomains = async () => {
    try { const d = await listDomains(); setDomains(d); if (d.length > 0 && !selectedDomain) setSelectedDomain(d[0].id); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  const loadRules = async (domainId: string) => {
    setLoading(true);
    try { const r = await listRules(domainId); setRules(r); } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  const handleCreate = async () => {
    if (!newRule.name.trim()) return;
    try { await createRule(selectedDomain, newRule); setShowForm(false); setNewRule({ name: "", description: "", condition: { pattern: "entity_match", parameters: {} }, action_type: "emit_alert", action_config: { message_template: "", severity: "medium" }, priority: 0 }); loadRules(selectedDomain); }
    catch (e: any) { alert(e.message); }
  };

  const severityColors: Record<string, string> = {
    critical: "bg-red-100 text-red-700 border-red-200",
    high: "bg-orange-100 text-orange-700 border-orange-200",
    medium: "bg-amber-100 text-amber-700 border-amber-200",
    low: "bg-green-100 text-green-700 border-green-200",
  };

  const executions = mockExecutions["any"] || [];

  return (
    <div className="p-8 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-slate-900 tracking-tight flex items-center gap-3">
            <Shield className="w-7 h-7 text-orange-500" /> 规则管理
          </h1>
          <p className="text-slate-500 text-sm mt-1">声明式规则引擎：条件匹配 + 动作触发</p>
        </div>
        <div className="flex items-center gap-3">
          <Select value={selectedDomain} onValueChange={setSelectedDomain}>
            <SelectTrigger className="w-48"><SelectValue placeholder="选择领域" /></SelectTrigger>
            <SelectContent>{domains.map(d => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
          </Select>
          <Dialog open={showForm} onOpenChange={setShowForm}>
            <DialogTrigger asChild><Button className="bg-blue-600 hover:bg-blue-700"><Plus className="w-4 h-4 mr-1" /> 新建规则</Button></DialogTrigger>
            <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
              <DialogHeader><DialogTitle>新建规则</DialogTitle></DialogHeader>
              <div className="space-y-4 mt-2">
                <div><Label>名称 *</Label><Input value={newRule.name} onChange={e => setNewRule({ ...newRule, name: e.target.value })} placeholder="如：P1事件告警" /></div>
                <div><Label>描述</Label><Textarea value={newRule.description} onChange={e => setNewRule({ ...newRule, description: e.target.value })} placeholder="规则用途" /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div><Label>匹配实体类型</Label><Input value={newRule.condition.parameters.entity_type || ""} onChange={e => setNewRule({ ...newRule, condition: { ...newRule.condition, parameters: { ...newRule.condition.parameters, entity_type: e.target.value } } })} placeholder="如：Incident" /></div>
                  <div><Label>属性条件</Label><Input value={newRule.condition.parameters.prop_status || ""} onChange={e => setNewRule({ ...newRule, condition: { ...newRule.condition, parameters: { ...newRule.condition.parameters, prop_status: e.target.value } } })} placeholder="如：P1-Critical" /></div>
                </div>
                <div><Label>动作类型</Label>
                  <Select value={newRule.action_type} onValueChange={v => setNewRule({ ...newRule, action_type: v })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="emit_alert">发送告警</SelectItem>
                      <SelectItem value="create_task">创建任务</SelectItem>
                      <SelectItem value="require_approval">需要审批</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div><Label>消息模板</Label><Input value={newRule.action_config.message_template} onChange={e => setNewRule({ ...newRule, action_config: { ...newRule.action_config, message_template: e.target.value } })} placeholder="触发时显示的消息" /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div><Label>严重级别</Label>
                    <Select value={newRule.action_config.severity} onValueChange={v => setNewRule({ ...newRule, action_config: { ...newRule.action_config, severity: v } })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>{["critical", "high", "medium", "low"].map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div><Label>优先级</Label><Input type="number" value={newRule.priority} onChange={e => setNewRule({ ...newRule, priority: parseInt(e.target.value) || 0 })} /></div>
                </div>
                <Button onClick={handleCreate} className="w-full">创建规则</Button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-xl p-4 flex items-center gap-3 text-red-700 text-sm"><AlertCircle className="w-5 h-5" /> {error}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-20"><Loader2 className="w-8 h-8 animate-spin text-blue-600" /></div>
      ) : rules.length === 0 ? (
        <div className="bg-white rounded-xl border border-dashed border-slate-300 p-16 text-center">
          <Shield className="w-12 h-12 mx-auto mb-3 text-slate-300" />
          <h3 className="text-lg font-medium text-slate-700 mb-1">暂无规则</h3>
          <p className="text-slate-400 text-sm mb-4">创建第一条规则来自动化处理流程</p>
          <Button size="sm" onClick={() => setShowForm(true)}><Plus className="w-4 h-4 mr-1" /> 新建规则</Button>
        </div>
      ) : (
        <div className="space-y-3">
          {rules.map(rule => {
            const isExpanded = expandedRule === rule.id;
            return (
              <div key={rule.id} className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                {/* Rule Header */}
                <div className="p-5 flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <h3 className="font-semibold text-slate-900">{rule.name}</h3>
                      {rule.is_draft ? <Badge className="bg-amber-50 text-amber-700 border-amber-200">草稿</Badge> :
                        rule.is_active ? <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200"><CheckCircle2 className="w-3 h-3 mr-1" />运行中</Badge> :
                          <Badge variant="outline" className="text-slate-400">已停用</Badge>}
                      <span className={`text-xs px-2 py-0.5 rounded-full border ${severityColors[rule.action_config?.severity || "medium"]}`}>{rule.action_config?.severity || "medium"}</span>
                      <span className="text-xs text-slate-400">v{rule.version}</span>
                    </div>
                    {rule.description && <p className="text-slate-500 text-sm mb-2">{rule.description}</p>}
                    <div className="flex items-center gap-4 text-xs text-slate-400">
                      <span>动作: <span className="text-slate-600 font-medium">{rule.action_type}</span></span>
                      <span>优先级: <span className="text-slate-600 font-medium">{rule.priority}</span></span>
                      <span className="flex items-center gap-1"><CheckCircle2 className="w-3 h-3 text-emerald-400" /> {rule.hit_count} 命中</span>
                      <span className="flex items-center gap-1"><XCircle className="w-3 h-3 text-red-400" /> {rule.false_positive_count} 误报</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 ml-4 flex-shrink-0">
                    <button onClick={() => setExpandedRule(isExpanded ? null : rule.id)} className="p-2 rounded-lg hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors" title="执行历史">
                      <History className="w-4 h-4" />
                    </button>
                    {rule.is_draft && <Button size="sm" variant="ghost" onClick={() => publishRule(rule.id).then(() => loadRules(selectedDomain))}><Send className="w-4 h-4 text-emerald-600" /></Button>}
                    {!rule.is_draft && <Button size="sm" variant="ghost" onClick={() => toggleRule(rule.id).then(() => loadRules(selectedDomain))}>
                      {rule.is_active ? <Pause className="w-4 h-4 text-amber-600" /> : <Play className="w-4 h-4 text-emerald-600" />}
                    </Button>}
                    <Button size="sm" variant="ghost" onClick={() => { if (confirm("确定删除？")) deleteRule(rule.id).then(() => loadRules(selectedDomain)); }}>
                      <Trash2 className="w-4 h-4 text-red-400" />
                    </Button>
                  </div>
                </div>

                {/* Execution History Panel */}
                {isExpanded && (
                  <div className="border-t border-slate-100 bg-slate-50/50 px-5 py-4">
                    <div className="flex items-center gap-2 mb-3">
                      <History className="w-4 h-4 text-slate-500" />
                      <h4 className="text-sm font-semibold text-slate-700">触发历史</h4>
                    </div>
                    {executions.length > 0 ? (
                      <div className="space-y-2">
                        {executions.map(ex => (
                          <div key={ex.id} className="bg-white rounded-lg border border-slate-200 p-3 flex items-start gap-3">
                            <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${ex.status === "triggered" ? "bg-red-400" : "bg-green-400"}`} />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 mb-0.5">
                                <span className="font-medium text-slate-800 text-sm">{ex.entity_name}</span>
                                <Badge variant="outline" className="text-[10px] h-5">{ex.entity_type}</Badge>
                                <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${severityColors[ex.severity]}`}>{ex.severity}</span>
                              </div>
                              <p className="text-xs text-slate-500">{ex.message}</p>
                              {ex.status === "dismissed" && (
                                <p className="text-xs text-green-600 mt-1">
                                  已处理 - {ex.dismissed_by}: {ex.dismissed_reason}
                                </p>
                              )}
                            </div>
                            <span className="text-xs text-slate-400 flex-shrink-0">{new Date(ex.created_at).toLocaleTimeString()}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-slate-400 text-center py-4">暂无触发记录</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
