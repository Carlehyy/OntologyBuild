import { useEffect, useState } from "react";
import { listDomains, listObjectTypes, listRelationTypes, createObjectType, createRelationType, deleteObjectType, deleteRelationType, createDomain } from "@/api/client";
import type { Domain, ObjectType, RelationType } from "@/types";
import { Plus, Trash2, Box, ArrowRight, AlertCircle, Loader2, BookOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

export default function Ontology() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [objectTypes, setObjectTypes] = useState<ObjectType[]>([]);
  const [relationTypes, setRelationTypes] = useState<RelationType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<"objects" | "relations">("objects");

  // Forms
  const [showObjForm, setShowObjForm] = useState(false);
  const [showRelForm, setShowRelForm] = useState(false);
  const [showDomainForm, setShowDomainForm] = useState(false);
  const [newObj, setNewObj] = useState({ name: "", description: "", color: "#3b82f6", icon: "box" });
  const [newRel, setNewRel] = useState({ name: "", description: "", source_type_id: "", target_type_id: "", is_directed: true, cardinality: "many_to_many" });
  const [newDomain, setNewDomain] = useState({ name: "", description: "" });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    loadDomains();
  }, []);

  useEffect(() => {
    if (selectedDomain) {
      loadDomainData(selectedDomain);
    }
  }, [selectedDomain]);

  const loadDomains = async () => {
    try {
      const d = await listDomains();
      setDomains(d);
      if (d.length > 0 && !selectedDomain) {
        setSelectedDomain(d[0].id);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const loadDomainData = async (domainId: string) => {
    setLoading(true);
    try {
      const [ots, rts] = await Promise.all([
        listObjectTypes(domainId),
        listRelationTypes(domainId),
      ]);
      setObjectTypes(ots);
      setRelationTypes(rts);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateObject = async () => {
    if (!newObj.name.trim()) return;
    try {
      await createObjectType(selectedDomain, newObj);
      setNewObj({ name: "", description: "", color: "#3b82f6", icon: "box" });
      setShowObjForm(false);
      loadDomainData(selectedDomain);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleCreateRelation = async () => {
    if (!newRel.name.trim() || !newRel.source_type_id || !newRel.target_type_id) return;
    try {
      await createRelationType(selectedDomain, newRel);
      setNewRel({ name: "", description: "", source_type_id: "", target_type_id: "", is_directed: true, cardinality: "many_to_many" });
      setShowRelForm(false);
      loadDomainData(selectedDomain);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleDeleteObject = async (id: string) => {
    if (!confirm("确定删除此对象类型？相关实体也将被删除。")) return;
    try {
      await deleteObjectType(id);
      loadDomainData(selectedDomain);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const handleDeleteRelation = async (id: string) => {
    if (!confirm("确定删除此关系类型？")) return;
    try {
      await deleteRelationType(id);
      loadDomainData(selectedDomain);
    } catch (e: any) {
      alert(e.message);
    }
  };

  const dataTypeLabels: Record<string, string> = {
    string: "字符串", integer: "整数", float: "浮点数", boolean: "布尔",
    date: "日期", enum: "枚举", text: "长文本",
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <BookOpen className="w-6 h-6" />
            本体管理
          </h1>
          <p className="text-gray-500 text-sm mt-1">定义对象类型、属性和关系类型</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={selectedDomain} onValueChange={setSelectedDomain}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="选择领域" />
            </SelectTrigger>
            <SelectContent>
              {domains.map((d) => (
                <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Dialog open={showDomainForm} onOpenChange={setShowDomainForm}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm"><Plus className="w-4 h-4" /></Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader><DialogTitle>创建新领域</DialogTitle></DialogHeader>
              <div className="space-y-3 mt-2">
                <div><Label>名称 *</Label><Input value={newDomain.name} onChange={e => setNewDomain({ ...newDomain, name: e.target.value })} placeholder="如：金融服务" /></div>
                <div><Label>描述</Label><Textarea value={newDomain.description} onChange={e => setNewDomain({ ...newDomain, description: e.target.value })} placeholder="描述领域范围" /></div>
                <Button onClick={async () => { if (!newDomain.name.trim()) return; setCreating(true); try { await createDomain(newDomain); setNewDomain({ name: "", description: "" }); setShowDomainForm(false); const d = await listDomains(); setDomains(d); } catch (e: any) { alert(e.message); } finally { setCreating(false); } }} disabled={creating} className="w-full">
                  {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : "创建"}
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-red-700 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1 w-fit mb-6">
        <button
          onClick={() => setActiveTab("objects")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
            activeTab === "objects" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
          }`}
        >
          对象类型 ({objectTypes.length})
        </button>
        <button
          onClick={() => setActiveTab("relations")}
          className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
            activeTab === "relations" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
          }`}
        >
          关系类型 ({relationTypes.length})
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
        </div>
      ) : activeTab === "objects" ? (
        <div>
          <div className="flex justify-end mb-4">
            <Dialog open={showObjForm} onOpenChange={setShowObjForm}>
              <DialogTrigger asChild>
                <Button size="sm"><Plus className="w-4 h-4 mr-1" /> 新建对象类型</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader><DialogTitle>新建对象类型</DialogTitle></DialogHeader>
                <div className="space-y-3 mt-2">
                  <div><Label>名称 *</Label><Input value={newObj.name} onChange={(e) => setNewObj({ ...newObj, name: e.target.value })} placeholder="如：Incident" /></div>
                  <div><Label>描述</Label><Textarea value={newObj.description} onChange={(e) => setNewObj({ ...newObj, description: e.target.value })} placeholder="对象类型的用途描述" /></div>
                  <div className="flex gap-3">
                    <div><Label>颜色</Label><Input type="color" value={newObj.color} onChange={(e) => setNewObj({ ...newObj, color: e.target.value })} className="w-16 h-9 p-1" /></div>
                    <div className="flex-1"><Label>图标</Label><Input value={newObj.icon} onChange={(e) => setNewObj({ ...newObj, icon: e.target.value })} placeholder="Lucide图标名" /></div>
                  </div>
                  <Button onClick={handleCreateObject} className="w-full">创建</Button>
                </div>
              </DialogContent>
            </Dialog>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {objectTypes.map((ot) => (
              <div key={ot.id} className="bg-white rounded-xl border border-gray-200 p-5 hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: ot.color + "20" }}>
                      <Box className="w-5 h-5" style={{ color: ot.color }} />
                    </div>
                    <div>
                      <h3 className="font-semibold text-gray-900">{ot.name}</h3>
                      <p className="text-gray-400 text-xs">v{ot.version}</p>
                    </div>
                  </div>
                  <button onClick={() => handleDeleteObject(ot.id)} className="text-gray-400 hover:text-red-500 transition-colors">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
                {ot.description && <p className="text-gray-500 text-sm mb-3">{ot.description}</p>}
                <div className="space-y-1.5">
                  <p className="text-xs text-gray-400 font-medium">属性 ({ot.properties.length})</p>
                  {ot.properties.map((prop) => (
                    <div key={prop.id} className="flex items-center justify-between text-sm py-1 px-2 bg-gray-50 rounded">
                      <span className="text-gray-700">{prop.name}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400">{dataTypeLabels[prop.data_type] || prop.data_type}</span>
                        {prop.is_required && <span className="text-xs text-red-400">*必填</span>}
                      </div>
                    </div>
                  ))}
                  {ot.properties.length === 0 && <p className="text-xs text-gray-400 italic">暂无属性</p>}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div>
          <div className="flex justify-end mb-4">
            <Dialog open={showRelForm} onOpenChange={setShowRelForm}>
              <DialogTrigger asChild>
                <Button size="sm"><Plus className="w-4 h-4 mr-1" /> 新建关系类型</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader><DialogTitle>新建关系类型</DialogTitle></DialogHeader>
                <div className="space-y-3 mt-2">
                  <div><Label>名称 *</Label><Input value={newRel.name} onChange={(e) => setNewRel({ ...newRel, name: e.target.value })} placeholder="如：affects" /></div>
                  <div><Label>描述</Label><Textarea value={newRel.description} onChange={(e) => setNewRel({ ...newRel, description: e.target.value })} placeholder="关系类型的用途描述" /></div>
                  <div className="grid grid-cols-2 gap-3">
                    <div><Label>源类型 *</Label>
                      <Select value={newRel.source_type_id} onValueChange={(v) => setNewRel({ ...newRel, source_type_id: v })}>
                        <SelectTrigger><SelectValue placeholder="选择" /></SelectTrigger>
                        <SelectContent>{objectTypes.map((ot) => <SelectItem key={ot.id} value={ot.id}>{ot.name}</SelectItem>)}</SelectContent>
                      </Select>
                    </div>
                    <div><Label>目标类型 *</Label>
                      <Select value={newRel.target_type_id} onValueChange={(v) => setNewRel({ ...newRel, target_type_id: v })}>
                        <SelectTrigger><SelectValue placeholder="选择" /></SelectTrigger>
                        <SelectContent>{objectTypes.map((ot) => <SelectItem key={ot.id} value={ot.id}>{ot.name}</SelectItem>)}</SelectContent>
                      </Select>
                    </div>
                  </div>
                  <Button onClick={handleCreateRelation} className="w-full">创建</Button>
                </div>
              </DialogContent>
            </Dialog>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500">
                <tr><th className="px-5 py-3 text-left">名称</th><th className="px-5 py-3 text-left">方向</th><th className="px-5 py-3 text-left">基数</th><th className="px-5 py-3 text-right">操作</th></tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {relationTypes.map((rt) => (
                  <tr key={rt.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3">
                      <div className="font-medium text-gray-900">{rt.name}</div>
                      {rt.description && <div className="text-gray-400 text-xs">{rt.description}</div>}
                    </td>
                    <td className="px-5 py-3">
                      <span className="text-gray-600">{rt.source_type_name}</span>
                      <ArrowRight className="w-3 h-3 inline mx-1 text-gray-400" />
                      <span className="text-gray-600">{rt.target_type_name}</span>
                    </td>
                    <td className="px-5 py-3 text-gray-500">{rt.cardinality}</td>
                    <td className="px-5 py-3 text-right">
                      <button onClick={() => handleDeleteRelation(rt.id)} className="text-gray-400 hover:text-red-500">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
                {relationTypes.length === 0 && (
                  <tr><td colSpan={4} className="px-5 py-8 text-center text-gray-400">暂无关系类型</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
