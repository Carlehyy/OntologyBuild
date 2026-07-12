import { useMemo, useState } from 'react';
import {
  XMarkIcon,
  CommandLineIcon,
  ClipboardDocumentIcon,
  CheckIcon,
  CubeIcon,
  BoltIcon,
  CodeBracketIcon,
  LinkIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';

interface ApiDocsProps {
  isOpen: boolean;
  onClose: () => void;
}

type TabType = 'sdk' | 'rest' | 'graphql' | 'functions';

export default function ApiDocs({ isOpen, onClose }: ApiDocsProps) {
  const ontology = useOntologyStore((s) => s.ontology);
  const [activeTab, setActiveTab] = useState<TabType>('sdk');
  const [copied, setCopied] = useState<string | null>(null);
  const [selectedObjectId, setSelectedObjectId] = useState<string>('');

  const objectTypes = ontology?.objectTypes || [];
  const actions = ontology?.actions || [];
  const functions = ontology?.functions || [];
  const links = ontology?.linkTypes || [];

  const selectedObject = objectTypes.find((o) => o.id === selectedObjectId) || objectTypes[0];

  const sdkCode = useMemo(() => {
    if (!selectedObject) return '';
    const pascal = toPascal(selectedObject.name);
    const props = selectedObject.properties;
    const pk = props.find((p) => p.id === selectedObject.primaryKey);

    return `// Auto-generated TypeScript SDK for ${selectedObject.displayName}
// Generated from Ontology: ${ontology?.name}

import { OntologyClient } from '@foundry/ontology-sdk';

export interface ${pascal} {
${props.map((p) => `  /** ${p.description || p.displayName} */
  ${p.name}${p.required ? '' : '?'}: ${tsType(p.type)};`).join('\n')}
}

export class ${pascal}Client {
  constructor(private client: OntologyClient) {}

  /** Get a ${selectedObject.displayName} by primary key */
  async get(${pk?.name || 'id'}: ${pk ? tsType(pk.type) : 'string'}): Promise<${pascal}> {
    return this.client.get<${pascal}>('/ontology/objects/${selectedObject.name}/' + ${pk?.name || 'id'});
  }

  /** List ${selectedObject.displayName} instances with pagination */
  async list(params?: { pageSize?: number; pageToken?: string }): Promise<{
    data: ${pascal}[];
    nextPageToken?: string;
  }> {
    return this.client.list<${pascal}>('/ontology/objects/${selectedObject.name}', params);
  }

  /** Search ${selectedObject.displayName} with filters */
  async search(filters: Partial<${pascal}>): Promise<${pascal}[]> {
    return this.client.search<${pascal}>('/ontology/objects/${selectedObject.name}', filters);
  }
${actions.filter((a) => a.objectTypeId === selectedObject.id).map((a) => `
  /** ${a.description || a.displayName} */
  async ${a.name}(params: {
${a.parameters.map((p) => `    ${p.name}${p.required ? '' : '?'}: ${tsType(p.type)};`).join('\n')}
  }): Promise<ActionResult> {
    return this.client.applyAction('${a.name}', params);
  }`).join('')}
}
`;
  }, [selectedObject, actions, ontology]);

  const restCode = useMemo(() => {
    if (!selectedObject) return '';
    const pk = selectedObject.properties.find((p) => p.id === selectedObject.primaryKey);
    return `# REST API for ${selectedObject.displayName}
# Base URL: /api/v1/ontology

## Objects
GET    /objects/${selectedObject.name}                    # List all
GET    /objects/${selectedObject.name}/{${pk?.name || 'id'}}}    # Get by PK
POST   /objects/${selectedObject.name}                    # Create
PATCH  /objects/${selectedObject.name}/{${pk?.name || 'id'}}}    # Update
DELETE /objects/${selectedObject.name}/{${pk?.name || 'id'}}}    # Delete
POST   /objects/${selectedObject.name}/search              # Search with filters

## Links
${links.filter((l) => l.sourceObjectTypeId === selectedObject.id || l.targetObjectTypeId === selectedObject.id).map((l) => {
  const isSource = l.sourceObjectTypeId === selectedObject.id;
  const other = isSource
    ? objectTypes.find((o) => o.id === l.targetObjectTypeId)
    : objectTypes.find((o) => o.id === l.sourceObjectTypeId);
  return `GET    /objects/${selectedObject.name}/{id}/${l.name}  # Get linked ${other?.displayName || l.name}`;
}).join('\n')}

## Actions
${actions.map((a) => `POST   /actions/${a.name}  # Execute: ${a.displayName}`).join('\n')}

## Functions
${functions.map((f) => `POST   /functions/${f.name}  # Call: ${f.displayName}`).join('\n')}
`;
  }, [selectedObject, actions, functions, links, objectTypes]);

  const graphqlCode = useMemo(() => {
    return `# GraphQL Schema (auto-generated)

type Query {
${objectTypes.map((o) => `  ${o.name}(id: ID!): ${toPascal(o.name)}
  ${pluralize(o.name)}(filter: ${toPascal(o.name)}Filter, page: PageInput): ${toPascal(o.name)}Connection!`).join('\n')}
}

type Mutation {
${actions.map((a) => `  ${a.name}(input: ${toPascal(a.name)}Input!): ActionResult!`).join('\n')}
${objectTypes.map((o) => `  create${toPascal(o.name)}(input: ${toPascal(o.name)}Input!): ${toPascal(o.name)}!
  update${toPascal(o.name)}(id: ID!, patch: ${toPascal(o.name)}Patch!): ${toPascal(o.name)}!
  delete${toPascal(o.name)}(id: ID!): Boolean!`).join('\n')}
}

${objectTypes.map((o) => `
type ${toPascal(o.name)} {
${o.properties.map((p) => p.source === 'computed' && p.functionId
  ? `  ${p.name}: ${gqlType(p.type)}!  # @computed(function: "${p.functionId}")`
  : `  ${p.name}: ${gqlType(p.type)}${p.required ? '!' : ''}`).join('\n')}
${links.filter((l) => l.sourceObjectTypeId === o.id).map((l) => {
  const target = objectTypes.find((ot) => ot.id === l.targetObjectTypeId);
  const isMany = l.cardinality.includes('many');
  return `  ${l.name}: ${target ? toPascal(target.name) : 'Object'}${isMany ? 'Connection!' : ''}`;
}).join('\n')}
}`).join('\n')}
`;
  }, [objectTypes, actions, functions, links]);

  const functionsCode = useMemo(() => {
    return functions.map((f) => {
      const ctx = f.functionType === 'object' ? 'obj' : 'objects';
      return `/**
 * ${f.description || f.displayName}
 * @function ${f.name}
 * @type ${f.functionType}
${f.targetObjectTypeId ? ` * @boundTo ${objectTypes.find((o) => o.id === f.targetObjectTypeId)?.name || f.targetObjectTypeId}` : ''}
 */
export async function ${f.name}(
  ${f.functionType === 'object' ? `${ctx}: OntologyObject,` : f.functionType === 'object_set' ? `${ctx}: OntologyObject[],` : ''}
  params: {
${f.parameters.map((p) => `    ${p.name}${p.required ? '' : '?'}: ${tsType(p.type)};`).join('\n')}
  },
  context: FunctionContext
): Promise<${tsType(f.returnType)}> {
${f.body}
}

// Invoke via API:
//   POST /functions/${f.name}
//   Body: { ${f.functionType === 'object' ? 'objectId, ' : ''}params: { ${f.parameters.map((p) => p.name).join(', ')} } }
`;
    }).join('\n\n');
  }, [functions, objectTypes]);

  const copy = async (code: string, id: string) => {
    await navigator.clipboard.writeText(code);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };

  const tabs: { id: TabType; label: string; icon: React.ElementType }[] = [
    { id: 'sdk', label: 'TypeScript SDK', icon: CodeBracketIcon },
    { id: 'rest', label: 'REST API', icon: CommandLineIcon },
    { id: 'graphql', label: 'GraphQL', icon: LinkIcon },
    { id: 'functions', label: 'Functions', icon: BoltIcon },
  ];

  if (!isOpen) return null;

  const activeCode = activeTab === 'sdk' ? sdkCode
    : activeTab === 'rest' ? restCode
    : activeTab === 'graphql' ? graphqlCode
    : functionsCode;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-4xl bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-gradient-to-r from-emerald-900/50 to-teal-900/50 border-b border-emerald-700/30">
          <div className="flex items-center gap-4">
            <div className="p-2.5 rounded-xl bg-gradient-to-br from-emerald-500/30 to-teal-600/30 border border-emerald-500/30">
              <CommandLineIcon className="w-7 h-7 text-emerald-300" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white flex items-center gap-2">
                API & SDK 文档
                <span className="px-2 py-0.5 text-xs font-normal bg-emerald-500/20 text-emerald-300 rounded-full">
                  Auto-Generated
                </span>
              </h2>
              <p className="text-sm text-emerald-300/60">从本体定义自动生成的接口文档和代码</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-white/10">
            <XMarkIcon className="w-6 h-6 text-gray-400 hover:text-white" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-700/50 bg-slate-800/30">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-5 py-3 text-sm font-medium transition-all ${
                activeTab === tab.id
                  ? 'text-emerald-300 border-b-2 border-emerald-400 bg-emerald-500/10'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-white/5'
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>

        {/* Object selector (for SDK/REST/GraphQL tabs) */}
        {(activeTab === 'sdk' || activeTab === 'rest') && (
          <div className="px-6 py-3 border-b border-slate-700/50 bg-slate-800/20 flex items-center gap-3">
            <CubeIcon className="w-4 h-4 text-emerald-400" />
            <span className="text-sm text-surface-400">对象类型：</span>
            <select
              value={selectedObject?.id || ''}
              onChange={(e) => setSelectedObjectId(e.target.value)}
              className="bg-slate-700 text-surface-200 text-sm rounded-lg px-3 py-1.5 border border-slate-600"
            >
              {objectTypes.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.icon || '📦'} {o.displayName} ({o.name})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Code display */}
        <div className="flex-1 overflow-hidden flex flex-col">
          <div className="flex items-center justify-between px-6 py-2 bg-slate-800/50 border-b border-slate-700/30">
            <span className="text-xs text-surface-500 font-mono">
              {activeTab === 'sdk' && `${selectedObject?.name || ''}.generated.ts`}
              {activeTab === 'rest' && 'rest-api.md'}
              {activeTab === 'graphql' && 'schema.graphql'}
              {activeTab === 'functions' && 'functions.ts'}
            </span>
            <button
              onClick={() => copy(activeCode, activeTab)}
              className="flex items-center gap-1 px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 text-surface-300 rounded transition-colors"
            >
              {copied === activeTab ? (
                <><CheckIcon className="w-3.5 h-3.5 text-emerald-400" /> 已复制</>
              ) : (
                <><ClipboardDocumentIcon className="w-3.5 h-3.5" /> 复制</>
              )}
            </button>
          </div>
          <pre className="flex-1 overflow-auto p-6 text-xs font-mono text-surface-300 bg-slate-950 leading-relaxed">
            <code>{activeCode}</code>
          </pre>
        </div>

        {/* Stats */}
        <div className="px-6 py-3 border-t border-slate-700/50 bg-slate-800/30 flex items-center gap-6 text-xs text-surface-500">
          <span>{objectTypes.length} 个对象类型</span>
          <span>{actions.length} 个动作</span>
          <span>{functions.length} 个函数</span>
          <span>{links.length} 个链接</span>
        </div>
      </div>
    </div>
  );
}

function tsType(t: string): string {
  switch (t) {
    case 'string': return 'string';
    case 'number': return 'number';
    case 'boolean': return 'boolean';
    case 'date':
    case 'datetime': return 'Date';
    case 'array': return 'unknown[]';
    case 'object': return 'Record<string, unknown>';
    case 'reference': return 'OntologyObject';
    case 'object_set': return 'OntologyObject[]';
    case 'void': return 'void';
    default: return 'unknown';
  }
}

function gqlType(t: string): string {
  switch (t) {
    case 'string': return 'String';
    case 'number': return 'Float';
    case 'boolean': return 'Boolean';
    case 'date':
    case 'datetime': return 'DateTime';
    case 'array': return '[JSON]';
    default: return 'JSON';
  }
}

function toPascal(s: string): string {
  return s.split(/[_\s]+/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join('');
}

function pluralize(s: string): string {
  if (s.endsWith('y')) return s.slice(0, -1) + 'ies';
  return s + 's';
}
