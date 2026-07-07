import { useEffect, useMemo, useRef, useState } from 'react';
import { MagnifyingGlassIcon, CubeIcon, LinkIcon, BoltIcon, CodeBracketIcon } from '@heroicons/react/24/outline';
import { useReactFlow } from '@xyflow/react';
import { useOntologyStore } from '../store/ontologyStore';

interface Result {
  kind: 'objectType' | 'linkType' | 'action' | 'function';
  id: string;
  label: string;
  sub: string;
}

const KIND_META = {
  objectType: { icon: CubeIcon, label: '对象', cls: 'text-indigo-400' },
  linkType: { icon: LinkIcon, label: '关系', cls: 'text-cyan-400' },
  action: { icon: BoltIcon, label: '动作', cls: 'text-yellow-400' },
  function: { icon: CodeBracketIcon, label: '函数', cls: 'text-pink-400' },
} as const;

/**
 * 全局搜索面板（Ctrl+K）：按名称搜索对象/关系/动作/函数与属性名，
 * 选中对象类型时画布平移聚焦到对应节点。
 */
export default function SearchPalette({ onClose }: { onClose: () => void }) {
  const ontology = useOntologyStore((s) => s.ontology);
  const nodes = useOntologyStore((s) => s.nodes);
  const { setSelectedNode, setSelectedEdge, setSelectedAction, setSelectedFunction, openPanel } = useOntologyStore();
  const { setCenter } = useReactFlow();

  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const results = useMemo<Result[]>(() => {
    if (!ontology) return [];
    const q = query.trim().toLowerCase();
    const out: Result[] = [];
    const match = (...fields: (string | undefined)[]) =>
      !q || fields.some((f) => f && f.toLowerCase().includes(q));

    for (const o of ontology.objectTypes) {
      const propHit = q && o.properties.find((p) => p.name.toLowerCase().includes(q) || (p.displayName || '').toLowerCase().includes(q));
      if (match(o.name, o.displayName) || propHit) {
        out.push({
          kind: 'objectType', id: o.id, label: o.displayName || o.name,
          sub: propHit ? `属性命中: ${propHit.name}` : o.name,
        });
      }
    }
    for (const l of ontology.linkTypes) {
      if (match(l.name, l.displayName)) out.push({ kind: 'linkType', id: l.id, label: l.displayName || l.name, sub: l.name });
    }
    for (const a of ontology.actions) {
      if (match(a.name, a.displayName)) out.push({ kind: 'action', id: a.id, label: a.displayName || a.name, sub: a.name });
    }
    for (const f of ontology.functions) {
      if (match(f.name, f.displayName)) out.push({ kind: 'function', id: f.id, label: f.displayName || f.name, sub: f.name });
    }
    return out.slice(0, 30);
  }, [ontology, query]);

  useEffect(() => { setActive(0); }, [query]);

  const pick = (r: Result) => {
    switch (r.kind) {
      case 'objectType': {
        setSelectedNode(r.id);
        const node = nodes.find((n) => n.id === r.id);
        if (node) {
          // 节点卡片约 280x120，中心点偏移一半
          setCenter(node.position.x + 140, node.position.y + 60, { zoom: 1, duration: 400 });
        }
        break;
      }
      case 'linkType':
        setSelectedEdge(r.id);
        openPanel('edit', 'linkType');
        break;
      case 'action':
        setSelectedAction(r.id);
        openPanel('edit', 'action');
        break;
      case 'function':
        setSelectedFunction(r.id);
        openPanel('edit', 'function');
        break;
    }
    onClose();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { onClose(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((v) => Math.min(v + 1, results.length - 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive((v) => Math.max(v - 1, 0)); }
    if (e.key === 'Enter' && results[active]) { e.preventDefault(); pick(results[active]); }
  };

  return (
    <div className="fixed inset-0 z-[120] flex items-start justify-center pt-[15vh] bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-800 border border-surface-600 rounded-2xl w-full max-w-lg mx-4 shadow-2xl overflow-hidden animate-fade-in"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-700">
          <MagnifyingGlassIcon className="w-5 h-5 text-surface-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="搜索对象 / 关系 / 动作 / 函数 / 属性名…"
            className="flex-1 bg-transparent outline-none text-sm text-surface-100 placeholder-surface-500"
          />
          <kbd className="px-1.5 py-0.5 rounded bg-surface-700 text-[10px] text-surface-400">Esc</kbd>
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-1.5">
          {results.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-surface-500">
              {query ? '没有匹配的内容' : '输入关键字开始搜索'}
            </p>
          )}
          {results.map((r, i) => {
            const meta = KIND_META[r.kind];
            return (
              <button
                key={`${r.kind}_${r.id}`}
                onClick={() => pick(r)}
                onMouseEnter={() => setActive(i)}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${
                  i === active ? 'bg-surface-700' : 'hover:bg-surface-700/60'
                }`}
              >
                <meta.icon className={`w-4 h-4 shrink-0 ${meta.cls}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-surface-100 truncate">{r.label}</div>
                  <div className="text-[11px] text-surface-500 font-mono truncate">{r.sub}</div>
                </div>
                <span className="text-[10px] text-surface-500 shrink-0">{meta.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
