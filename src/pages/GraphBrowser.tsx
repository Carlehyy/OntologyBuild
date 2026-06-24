import { useEffect, useState, useRef } from "react";
import { listDomains, getGraphVisualization, getGraphStats, searchGraph } from "@/api/client";
import type { Domain, GraphData, GraphNode } from "@/types";
import { GitBranch, Search, Loader2, AlertCircle, ZoomIn, ZoomOut, Maximize2, Info, X } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
// @ts-ignore
import * as d3 from "d3";

export default function GraphBrowser() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<string>("");
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<any>(null);

  useEffect(() => { loadDomains(); }, []);
  useEffect(() => { if (selectedDomain) loadGraph(selectedDomain); }, [selectedDomain]);

  const loadDomains = async () => {
    try { const d = await listDomains(); setDomains(d); if (d.length > 0 && !selectedDomain) setSelectedDomain(d[0].id); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  const loadGraph = async (domainId: string) => {
    setLoading(true);
    try { const [g, s] = await Promise.all([getGraphVisualization(domainId), getGraphStats(domainId)]); setGraphData(g); setStats(s); }
    catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim() || !selectedDomain) return;
    try {
      const result = await searchGraph(selectedDomain, searchQuery);
      if (result.results.length > 0) {
        const foundIds = new Set(result.results.map((r: any) => r.id));
        setGraphData(prev => ({ ...prev, nodes: prev.nodes.map((n: any) => ({ ...n, _highlight: foundIds.has(n.id) })) }));
      }
    } catch { /* ignore */ }
  };

  const handleZoomIn = () => { if (svgRef.current && zoomRef.current) { d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 1.5); } };
  const handleZoomOut = () => { if (svgRef.current && zoomRef.current) { d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 0.667); } };
  const handleReset = () => { if (svgRef.current && zoomRef.current) { d3.select(svgRef.current).transition().duration(500).call(zoomRef.current.transform, d3.zoomIdentity); } };

  // D3 force-directed graph
  useEffect(() => {
    if (!svgRef.current || graphData.nodes.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const container = containerRef.current;
    if (!container) return;
    const width = container.clientWidth;
    const height = container.clientHeight;
    svg.attr("viewBox", `0 0 ${width} ${height}`);

    const g = svg.append("g");

    // Arrow markers
    svg.append("defs").selectAll("marker")
      .data(["arrow"])
      .join("marker")
      .attr("id", (d: string) => d)
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 28)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", "#94a3b8");

    const links = graphData.edges.map((e: any) => ({ source: e.source, target: e.target, label: e.label, id: e.id }));

    const simulation = d3.forceSimulation(graphData.nodes as unknown[])
      .force("link", d3.forceLink(links).id((d: any) => d.id).distance(120))
      .force("charge", d3.forceManyBody().strength(-400))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(35));

    // Links
    const linkGroup = g.append("g");
    const link = linkGroup.selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke", "#cbd5e1")
      .attr("stroke-width", 1.5)
      .attr("marker-end", "url(#arrow)");

    const linkLabel = linkGroup.selectAll("text")
      .data(links)
      .join("text")
      .text((d: any) => d.label)
      .attr("font-size", "9px")
      .attr("fill", "#94a3b8")
      .attr("text-anchor", "middle")
      .attr("dy", -3);

    // Nodes
    const nodeGroup = g.selectAll("g.node")
      .data(graphData.nodes)
      .join("g")
      .attr("class", "node")
      .style("cursor", "pointer")
      .on("click", (_event: any, d: any) => { setSelectedNode(prev => prev?.id === d.id ? null : d as GraphNode); })
      .call(d3.drag()
        .on("start", (event: any, d: any) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (event: any, d: any) => { d.fx = event.x; d.fy = event.y; })
        .on("end", (event: any, d: any) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

    // Node glow
    nodeGroup.append("circle").attr("r", 22).attr("fill", (d: any) => d.color).attr("opacity", 0.12);
    // Node outer
    nodeGroup.append("circle").attr("r", 18).attr("fill", "white").attr("stroke", (d: any) => d.color).attr("stroke-width", 2.5);
    // Node inner
    nodeGroup.append("circle").attr("r", 7).attr("fill", (d: any) => d.color);
    // Label
    nodeGroup.append("text").text((d: any) => d.label).attr("dy", 32).attr("text-anchor", "middle").attr("font-size", "10px").attr("font-weight", "600").attr("fill", "#334155");
    // Type label
    nodeGroup.append("text").text((d: any) => d.type).attr("dy", 44).attr("text-anchor", "middle").attr("font-size", "8px").attr("fill", "#94a3b8");

    simulation.on("tick", () => {
      link.attr("x1", (d: any) => d.source.x).attr("y1", (d: any) => d.source.y).attr("x2", (d: any) => d.target.x).attr("y2", (d: any) => d.target.y);
      linkLabel.attr("x", (d: any) => (d.source.x + d.target.x) / 2).attr("y", (d: any) => (d.source.y + d.target.y) / 2);
      nodeGroup.attr("transform", (d: any) => `translate(${d.x},${d.y})`);
    });

    // Zoom
    const zoom = d3.zoom().scaleExtent([0.1, 4]).on("zoom", (event: any) => { g.attr("transform", event.transform.toString()); });
    svg.call(zoom);
    zoomRef.current = zoom;

    return () => { simulation.stop(); };
  }, [graphData]);

  return (
    <div className="p-8 max-w-7xl mx-auto h-[calc(100vh-2rem)] flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-3xl font-bold text-slate-900 tracking-tight flex items-center gap-3">
            <GitBranch className="w-7 h-7 text-purple-500" /> 图谱浏览
          </h1>
          <p className="text-slate-500 text-sm mt-1">力导向图可视化 · 滚轮缩放 · 拖拽节点</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Input placeholder="搜索实体..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)} onKeyDown={e => e.key === "Enter" && handleSearch()} className="w-48 h-9" />
            <button onClick={handleSearch} className="p-2 rounded-lg bg-slate-100 hover:bg-slate-200 transition-colors"><Search className="w-4 h-4 text-slate-600" /></button>
          </div>
          <Select value={selectedDomain} onValueChange={setSelectedDomain}>
            <SelectTrigger className="w-48 h-9"><SelectValue placeholder="选择领域" /></SelectTrigger>
            <SelectContent>{domains.map(d => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
          </Select>
        </div>
      </div>

      {error && <div className="mb-4 bg-red-50 border border-red-200 rounded-xl p-3 flex items-center gap-2 text-red-700 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}

      {stats && (
        <div className="flex items-center gap-5 mb-4 px-4 py-2.5 bg-white rounded-lg border border-slate-200 text-sm">
          <span className="text-slate-500">实体 <strong className="text-slate-800">{stats.entity_count}</strong></span>
          <span className="text-slate-300">|</span>
          <span className="text-slate-500">关系 <strong className="text-slate-800">{stats.relation_count}</strong></span>
          <span className="text-slate-300">|</span>
          <div className="flex items-center gap-3">
            {stats.type_breakdown?.map((t: any) => (
              <span key={t.type_id} className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full ring-2 ring-white" style={{ backgroundColor: t.color }} />
                <span className="text-slate-500">{t.type_name}</span>
                <span className="text-slate-400">({t.count})</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div ref={containerRef} className="flex-1 bg-slate-50 rounded-xl border border-slate-200 overflow-hidden relative">
        {loading ? (
          <div className="flex items-center justify-center h-full"><Loader2 className="w-8 h-8 animate-spin text-blue-600" /></div>
        ) : graphData.nodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-400">
            <GitBranch className="w-16 h-16 mb-4 opacity-30" />
            <p className="text-lg font-medium text-slate-500 mb-1">该领域暂无图谱数据</p>
            <p className="text-sm text-slate-400 mb-4">前往文档抽取上传文档并运行抽取</p>
            <button onClick={() => window.location.hash = "/extraction"} className="text-blue-600 text-sm font-medium hover:underline">去上传文档 →</button>
          </div>
        ) : (
          <>
            <svg ref={svgRef} className="w-full h-full" style={{ background: "linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%)" }} />
            <div className="absolute bottom-5 right-5 flex flex-col gap-1.5">
              {[{ icon: ZoomIn, onClick: handleZoomIn, label: "放大" }, { icon: ZoomOut, onClick: handleZoomOut, label: "缩小" }, { icon: Maximize2, onClick: handleReset, label: "重置" }].map(btn => (
                <button key={btn.label} onClick={btn.onClick} title={btn.label} className="p-2.5 bg-white rounded-lg shadow-md border border-slate-200 hover:bg-slate-50 hover:shadow-lg transition-all text-slate-600">
                  <btn.icon className="w-4 h-4" />
                </button>
              ))}
            </div>
            <div className="absolute top-4 left-4 bg-white/90 backdrop-blur rounded-lg shadow-sm border border-slate-200 px-3 py-2 text-xs">
              <div className="flex items-center gap-1.5 text-slate-500 mb-1"><Info className="w-3 h-3" /><span className="font-medium">操作提示</span></div>
              <div className="text-slate-400">滚轮缩放 · 拖拽节点 · 点击查看详情</div>
            </div>
            {selectedNode && (
              <div className="absolute top-4 right-4 w-72 bg-white/95 backdrop-blur rounded-xl shadow-xl border border-slate-200 p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2.5">
                    <div className="w-4 h-4 rounded-full" style={{ backgroundColor: selectedNode.color }} />
                    <h3 className="font-bold text-slate-900">{selectedNode.label}</h3>
                  </div>
                  <button onClick={() => setSelectedNode(null)} className="p-1 rounded hover:bg-slate-100 text-slate-400"><X className="w-4 h-4" /></button>
                </div>
                <div className="space-y-2.5 text-sm">
                  <div className="flex justify-between"><span className="text-slate-400">类型</span><span className="font-medium text-slate-700">{selectedNode.type}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">置信度</span>
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${selectedNode.confidence * 100}%`, backgroundColor: selectedNode.confidence > 0.8 ? "#10b981" : selectedNode.confidence > 0.5 ? "#f59e0b" : "#ef4444" }} />
                      </div>
                      <span className="text-slate-600">{(selectedNode.confidence * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                  <div className="flex justify-between"><span className="text-slate-400">已验证</span><span className={selectedNode.is_verified ? "text-emerald-600 font-medium" : "text-amber-600"}>{selectedNode.is_verified ? "是" : "否"}</span></div>
                  {Object.entries(selectedNode.properties).length > 0 && (
                    <div className="pt-3 border-t border-slate-100">
                      <p className="text-xs text-slate-400 font-medium mb-2">属性</p>
                      {Object.entries(selectedNode.properties).map(([k, v]) => (
                        <div key={k} className="flex justify-between py-0.5"><span className="text-slate-400 text-xs">{k}</span><span className="text-slate-600 text-xs">{String(v)}</span></div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
