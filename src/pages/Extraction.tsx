import { useEffect, useState, useRef } from "react";
import { listDomains, listDocuments, uploadDocument, runExtraction, getExtractionResults, reviewExtraction } from "@/api/client";
import type { Domain, Document, ExtractionResult } from "@/types";
import { FileText, Upload, Play, CheckCircle, XCircle, AlertCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

const statusLabels: Record<string, { text: string; color: string }> = {
  pending: { text: "待处理", color: "bg-gray-100 text-gray-600" },
  processing: { text: "处理中", color: "bg-blue-100 text-blue-600" },
  completed: { text: "已完成", color: "bg-green-100 text-green-600" },
  failed: { text: "失败", color: "bg-red-100 text-red-600" },
  reviewed: { text: "已审阅", color: "bg-purple-100 text-purple-600" },
};

export default function Extraction() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [documents, setDocuments] = useState<Document[]>([]);
  const [results, setResults] = useState<Record<string, ExtractionResult[]>>({});
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [extracting, setExtracting] = useState<string | null>(null);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { loadDomains(); }, []);
  useEffect(() => { if (selectedDomain) loadDocuments(selectedDomain); }, [selectedDomain]);

  const loadDomains = async () => {
    try { const d = await listDomains(); setDomains(d); if (d.length > 0 && !selectedDomain) setSelectedDomain(d[0].id); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  const loadDocuments = async (domainId: string) => {
    setLoading(true);
    try { const docs = await listDocuments(domainId); setDocuments(docs); } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !selectedDomain) return;
    setUploading(true);
    try {
      await uploadDocument(selectedDomain, file);
      loadDocuments(selectedDomain);
    } catch (e: any) { alert(e.message); }
    finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = ""; }
  };

  const handleExtract = async (docId: string) => {
    setExtracting(docId);
    try {
      await runExtraction(docId);
      // Load results
      const r = await getExtractionResults(docId);
      setResults(prev => ({ ...prev, [docId]: r }));
      loadDocuments(selectedDomain);
    } catch (e: any) { alert(e.message); }
    finally { setExtracting(null); }
  };

  const handleReview = async (resultId: string, action: string) => {
    try {
      await reviewExtraction({ result_id: resultId, action });
      // Refresh results for the document
      const docId = Object.entries(results).find(([_, r]) => r.some(x => x.id === resultId))?.[0];
      if (docId) {
        const r = await getExtractionResults(docId);
        setResults(prev => ({ ...prev, [docId]: r }));
      }
    } catch (e: any) { alert(e.message); }
  };

  const loadResults = async (docId: string) => {
    try {
      const r = await getExtractionResults(docId);
      setResults(prev => ({ ...prev, [docId]: r }));
    } catch (e: any) { /* ignore */ }
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2"><FileText className="w-6 h-6" /> 文档抽取</h1>
          <p className="text-gray-500 text-sm mt-1">上传文档，运行LLM抽取候选实体和关系</p>
        </div>
        <div className="flex items-center gap-3">
          <Select value={selectedDomain} onValueChange={setSelectedDomain}>
            <SelectTrigger className="w-48"><SelectValue placeholder="选择领域" /></SelectTrigger>
            <SelectContent>{domains.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
          </Select>
          <input type="file" ref={fileInputRef} onChange={handleUpload} className="hidden" accept=".txt,.md,.csv,.pdf,.docx" />
          <Button size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading || !selectedDomain}>
            {uploading ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Upload className="w-4 h-4 mr-1" />}
            上传文档
          </Button>
        </div>
      </div>

      {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-red-700 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-blue-600" /></div>
      ) : documents.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <FileText className="w-12 h-12 mx-auto mb-3 opacity-40" />
          <p>暂无文档，请上传</p>
          <p className="text-xs mt-1">支持 .txt, .md, .csv, .pdf, .docx</p>
        </div>
      ) : (
        <div className="space-y-4">
          {documents.map((doc) => {
            const status = statusLabels[doc.status] || statusLabels.pending;
            const docResults = results[doc.id] || [];
            const showResults = docResults.length > 0;
            return (
              <div key={doc.id} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                <div className="p-4 flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <h3 className="font-medium text-gray-900 truncate">{doc.original_filename}</h3>
                      <Badge className={status.color}>{status.text}</Badge>
                    </div>
                    <div className="flex items-center gap-4 mt-1 text-xs text-gray-400">
                      <span>{doc.file_size ? `${(doc.file_size / 1024).toFixed(1)} KB` : "-"}</span>
                      <span>{doc.mime_type || "-"}</span>
                      <span>实体: {doc.extracted_entities_count}</span>
                      <span>关系: {doc.extracted_relations_count}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-4">
                    {doc.status === "completed" && !showResults && (
                      <Button size="sm" variant="ghost" onClick={() => loadResults(doc.id)}>查看结果</Button>
                    )}
                    {doc.status === "completed" && (
                      <Button size="sm" variant="outline" onClick={() => handleExtract(doc.id)} disabled={extracting === doc.id}>
                        {extracting === doc.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                        重新抽取
                      </Button>
                    )}
                    {doc.content_text && doc.status === "pending" && (
                      <Button size="sm" onClick={() => handleExtract(doc.id)} disabled={extracting === doc.id}>
                        {extracting === doc.id ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Play className="w-3.5 h-3.5 mr-1" />}
                        运行抽取
                      </Button>
                    )}
                  </div>
                </div>

                {/* Extraction Results */}
                {showResults && (
                  <div className="border-t border-gray-100 px-4 py-3 bg-gray-50/50">
                    <p className="text-xs font-medium text-gray-500 mb-2">抽取结果 ({docResults.length})</p>
                    <div className="space-y-2">
                      {docResults.map((result) => (
                        <div key={result.id} className="bg-white rounded-lg border p-3">
                          <div className="flex items-start justify-between">
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <Badge variant="outline" className={result.result_type === "entity" ? "text-blue-600" : "text-purple-600"}>
                                  {result.result_type === "entity" ? "实体" : "关系"}
                                </Badge>
                                <span className="font-medium text-gray-900">{result.candidate_name || `${result.candidate_source_name} → ${result.candidate_target_name}`}</span>
                                {result.candidate_object_type_name && <span className="text-xs text-gray-400">({result.candidate_object_type_name})</span>}
                              </div>
                              {result.llm_reasoning && <p className="text-xs text-gray-500 mt-1">{result.llm_reasoning}</p>}
                              {result.confidence !== null && (
                                <div className="flex items-center gap-2 mt-1">
                                  <div className="w-20 h-1.5 bg-gray-200 rounded-full overflow-hidden">
                                    <div className="h-full bg-blue-500 rounded-full" style={{ width: `${result.confidence * 100}%` }} />
                                  </div>
                                  <span className="text-xs text-gray-400">{(result.confidence * 100).toFixed(0)}%</span>
                                </div>
                              )}
                            </div>
                            {result.status === "pending" && (
                              <div className="flex items-center gap-1 ml-3">
                                <button onClick={() => handleReview(result.id, "approved")} className="p-1.5 rounded hover:bg-green-50 text-green-600" title="通过"><CheckCircle className="w-4 h-4" /></button>
                                <button onClick={() => handleReview(result.id, "rejected")} className="p-1.5 rounded hover:bg-red-50 text-red-600" title="拒绝"><XCircle className="w-4 h-4" /></button>
                              </div>
                            )}
                            {result.status !== "pending" && (
                              <Badge variant="outline" className={result.status === "approved" ? "text-green-600" : result.status === "rejected" ? "text-red-600" : "text-gray-500"}>
                                {result.status === "approved" ? "已通过" : result.status === "rejected" ? "已拒绝" : result.status}
                              </Badge>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
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
