import { useEffect, useState } from "react";
import { getSystemConfig, seedDatabase } from "@/api/client";
import type { SystemConfig } from "@/types";
import { Settings as SettingsIcon, Brain, Database, RefreshCw, AlertCircle, Loader2, CheckCircle, XCircle, Sparkles, Shield } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function Settings() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [error, setError] = useState("");
  const [seedResult, setSeedResult] = useState<any>(null);

  useEffect(() => { loadConfig(); }, []);

  const loadConfig = async () => {
    try { const c = await getSystemConfig(); setConfig(c); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  const handleSeed = async () => {
    if (!confirm("确定重新填充种子数据？这将创建示例领域和配置。")) return;
    setSeeding(true);
    try { const r = await seedDatabase(); setSeedResult(r); loadConfig(); }
    catch (e: any) { setSeedResult({ error: e.message }); }
    finally { setSeeding(false); }
  };

  if (loading) {
    return <div className="flex items-center justify-center h-full"><Loader2 className="w-6 h-6 animate-spin text-blue-600" /></div>;
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2"><SettingsIcon className="w-6 h-6" /> 系统设置</h1>
        <p className="text-gray-500 text-sm mt-1">系统配置与维护</p>
      </div>

      {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-red-700 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}

      {config && (
        <div className="space-y-6">
          {/* System Info */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-900 mb-4">系统信息</h2>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div><span className="text-gray-500">应用名称</span><p className="font-medium text-gray-900">{config.app_name}</p></div>
              <div><span className="text-gray-500">版本</span><p className="font-medium text-gray-900">{config.app_version}</p></div>
            </div>
          </div>

          {/* LLM Config */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-900 mb-4 flex items-center gap-2"><Brain className="w-4 h-4" /> LLM 配置</h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-gray-600 text-sm">提供商</span>
                <Badge variant="outline">{config.llm_provider}</Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-600 text-sm">模型</span>
                <span className="text-sm text-gray-900">{config.llm_model}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-600 text-sm">状态</span>
                {config.llm_available ? (
                  <span className="flex items-center gap-1 text-green-600 text-sm"><CheckCircle className="w-4 h-4" /> 可用</span>
                ) : (
                  <span className="flex items-center gap-1 text-amber-600 text-sm"><XCircle className="w-4 h-4" /> 不可用（使用确定性兜底）</span>
                )}
              </div>
            </div>
          </div>

          {/* Features */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-900 mb-4">功能状态</h2>
            <div className="space-y-2">
              {Object.entries(config.features).map(([key, enabled]) => (
                <div key={key} className="flex items-center justify-between py-1.5">
                  <span className="text-gray-600 text-sm capitalize">{key}</span>
                  {enabled ? (
                    <Badge className="bg-green-100 text-green-700 hover:bg-green-100">
                      <Sparkles className="w-3 h-3 mr-1" />已启用
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-gray-400">
                      <Shield className="w-3 h-3 mr-1" />未启用
                    </Badge>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Maintenance */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <h2 className="font-semibold text-gray-900 mb-4 flex items-center gap-2"><Database className="w-4 h-4" /> 数据维护</h2>
            <div className="flex items-center gap-4">
              <Button onClick={handleSeed} disabled={seeding} variant="outline">
                {seeding ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <RefreshCw className="w-4 h-4 mr-1" />}
                重新填充种子数据
              </Button>
            </div>
            {seedResult && (
              <div className={`mt-3 p-3 rounded-lg text-sm ${seedResult.error ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700"}`}>
                {seedResult.error || `成功: ${seedResult.message} - ${seedResult.domain_name} (${seedResult.entities} 实体, ${seedResult.rules} 规则)`}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
