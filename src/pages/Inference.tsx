import { useState, useEffect } from "react";
import { listDomains, runInference } from "@/api/client";
import type { Domain } from "@/types";
import { Brain, Send, Loader2, AlertCircle, Shield, Sparkles, ThumbsUp, ThumbsDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

export default function Inference() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [useRules, setUseRules] = useState(true);
  const [useLlm, setUseLlm] = useState(true);
  const [feedbackGiven, setFeedbackGiven] = useState(false);

  useEffect(() => {
    listDomains().then(d => { setDomains(d); if (d.length > 0) setSelectedDomain(d[0].id); }).catch(() => {});
  }, []);

  const handleQuery = async () => {
    if (!query.trim() || !selectedDomain) return;
    setLoading(true);
    setError("");
    setResult(null);
    setFeedbackGiven(false);
    try {
      const r = await runInference({ domain_id: selectedDomain, query, use_rules: useRules, use_llm: useLlm });
      setResult(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleFeedback = (_verdict: string) => {
    setFeedbackGiven(true);
    // In a real app, would call the feedback API
  };

  const confidenceColor = (c: number) => {
    if (c >= 0.8) return "text-green-600";
    if (c >= 0.5) return "text-yellow-600";
    return "text-red-600";
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2"><Brain className="w-6 h-6" /> 推理问答</h1>
        <p className="text-gray-500 text-sm mt-1">基于规则+LLM对知识图谱进行智能问答</p>
      </div>

      {/* Query Input */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 mb-6">
        <div className="flex items-center gap-3 mb-4">
          <Select value={selectedDomain} onValueChange={setSelectedDomain}>
            <SelectTrigger className="w-48"><SelectValue placeholder="选择领域" /></SelectTrigger>
            <SelectContent>{domains.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
          </Select>
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={useRules} onChange={(e) => setUseRules(e.target.checked)} className="rounded" />
              <Shield className="w-3.5 h-3.5 text-orange-500" />
              <span className="text-gray-600">规则</span>
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} className="rounded" />
              <Sparkles className="w-3.5 h-3.5 text-purple-500" />
              <span className="text-gray-600">LLM</span>
            </label>
          </div>
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="输入你的问题，如：有哪些P1级别的事件？"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleQuery()}
            className="flex-1"
          />
          <Button onClick={handleQuery} disabled={loading || !query.trim()}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-red-700 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-4">
          {/* Answer */}
          <div className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-purple-500" />
                <h3 className="font-semibold text-gray-900">回答</h3>
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-sm font-medium ${confidenceColor(result.confidence)}`}>
                  置信度: {(result.confidence * 100).toFixed(0)}%
                </span>
                {!feedbackGiven ? (
                  <div className="flex items-center gap-1">
                    <button onClick={() => handleFeedback("useful")} className="p-1.5 rounded hover:bg-green-50 text-gray-400 hover:text-green-600"><ThumbsUp className="w-4 h-4" /></button>
                    <button onClick={() => handleFeedback("false_positive")} className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-600"><ThumbsDown className="w-4 h-4" /></button>
                  </div>
                ) : (
                  <span className="text-xs text-green-600">反馈已记录</span>
                )}
              </div>
            </div>
            <div className="text-gray-700 whitespace-pre-wrap text-sm leading-relaxed">{result.answer}</div>
            {result.reasoning && <p className="text-xs text-gray-400 mt-2">{result.reasoning}</p>}
          </div>

          {/* Rule Hits */}
          {result.rule_hits && result.rule_hits.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center gap-2 mb-3">
                <Shield className="w-4 h-4 text-orange-500" />
                <h3 className="font-semibold text-gray-900">规则命中 ({result.rule_hits.length})</h3>
              </div>
              <div className="space-y-2">
                {result.rule_hits.map((hit: any, i: number) => (
                  <div key={i} className="flex items-start gap-3 p-3 bg-orange-50 rounded-lg">
                    <div className="w-6 h-6 rounded-full bg-orange-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                      <span className="text-xs font-medium text-orange-600">{i + 1}</span>
                    </div>
                    <div>
                      <p className="font-medium text-gray-900 text-sm">{hit.rule_name}</p>
                      <p className="text-gray-500 text-xs mt-0.5">{hit.message}</p>
                      <Badge variant="outline" className="mt-1 text-xs">{hit.severity}</Badge>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
