import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { LoadingState } from '@/components/ui/LoadingState'
import { ontologyApi, modelApi } from '@/api/ontologies'
import { Send, Bot, UserCircle, Sparkles, Database, Zap, Lightbulb, ChevronDown, BrainCircuit } from 'lucide-react'
import axios from 'axios'

function getToken() { return localStorage.getItem('token') || '' }

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: { title: string; type: string }[]
  cypher?: string
  isLoading?: boolean
}

export default function RagQAStandalonePage() {
  // -- 模型选择 --
  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ['models'], queryFn: () => modelApi.list() as any,
  })
  const llmModels = (models as any[]).filter((m: any) => m.config_type === 'llm' || !m.config_type)
  const [selectedModelId, setSelectedModelId] = useState('')
  useEffect(() => {
    if (llmModels.length > 0 && !selectedModelId) setSelectedModelId(llmModels[0].id)
  }, [llmModels, selectedModelId])

  // -- 本体选择 --
  const { data: ontologies = [], isLoading: ontologiesLoading } = useQuery({
    queryKey: ['ontologies'], queryFn: () => ontologyApi.list() as any,
  })
  const ontologyList = (ontologies as any)?.items || ontologies || []
  const [selectedOntologyId, setSelectedOntologyId] = useState('')
  useEffect(() => {
    if (ontologyList.length > 0 && !selectedOntologyId) setSelectedOntologyId(ontologyList[0].id)
  }, [ontologyList, selectedOntologyId])

  // -- 对话状态 --
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [isTyping, setIsTyping] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const selectedOntology = ontologyList.find((o: any) => o.id === selectedOntologyId)

  const suggestedQuestions = [
    '这个本体包含哪些实体类型？',
    '总结当前知识图谱的核心关系',
    '有哪些规则正在运行？',
    '分析实体之间的关键路径',
  ]

  const handleSend = async () => {
    if (!query.trim() || isTyping) return
    if (!selectedOntologyId) return

    const userMsg: Message = { id: Date.now().toString(), role: 'user', content: query.trim() }
    setMessages(prev => [...prev, userMsg])
    const currentQuery = query.trim()
    setQuery('')
    setIsTyping(true)

    const loadingId = (Date.now() + 1).toString()
    setMessages(prev => [...prev, { id: loadingId, role: 'assistant', content: '', isLoading: true }])

    try {
      const r = await axios.post(`/api/v2/ontologies/${selectedOntologyId}/graph/ask`, {
        question: currentQuery,
        model_id: selectedModelId || undefined,
      }, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      const data = r.data
      const results = data.results || []

      let answer = ''
      let sources: { title: string; type: string }[] = []

      if (results.length > 0) {
        const names = results.slice(0, 10).map((r: any) => {
          const props = r.n?.properties || r.properties || {}
          return props.name_cn || props.name || '未知'
        }).filter(Boolean)
        answer = `找到以下${results.length}个相关结果：\n${names.join('、')}`
        if (data.fallback === 'entity_search') {
          sources = [{ title: '实体关键词匹配', type: 'search' }]
        } else {
          sources = [{ title: '知识图谱查询', type: 'graph' }]
        }
      } else {
        answer = '未找到与该问题直接相关的结果。您可以尝试：\n• 使用更具体的关键词\n• 询问已知的实体或关系\n• 检查当前本体是否包含相关数据'
      }

      if (data.cypher) {
        sources.push({ title: data.cypher.slice(0, 50) + '...', type: 'cypher' })
      }

      setMessages(prev => prev.map(m => m.id === loadingId ? {
        ...m, isLoading: false, content: answer, sources,
      } : m))
    } catch {
      setMessages(prev => prev.map(m => m.id === loadingId ? {
        ...m, isLoading: false, content: '查询服务暂时不可用，请稍后重试。',
      } : m))
    } finally { setIsTyping(false) }
  }

  const clearChat = () => setMessages([])

  if (modelsLoading || ontologiesLoading) return <LoadingState message="加载配置..." />

  return (
    <div className="flex flex-col h-[calc(100vh-64px)]">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center">
            <BrainCircuit size={16} className="text-white" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">智能问答</h2>
            <p className="text-[11px] text-[var(--color-text-tertiary)]">基于知识图谱 + LLM 的智能问答</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* 模型选择器 */}
          <div className="relative">
            <select
              value={selectedModelId}
              onChange={e => setSelectedModelId(e.target.value)}
              className="appearance-none pl-3 pr-8 py-1.5 text-xs border border-[var(--color-border)] rounded-lg bg-[var(--color-bg-base)] text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] cursor-pointer"
            >
              {llmModels.length === 0 && <option value="">默认模型</option>}
              {llmModels.map((m: any) => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
            <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)] pointer-events-none" />
          </div>

          {/* 本体选择器 */}
          <div className="relative">
            <select
              value={selectedOntologyId}
              onChange={e => { setSelectedOntologyId(e.target.value); clearChat() }}
              className="appearance-none pl-3 pr-8 py-1.5 text-xs border border-[var(--color-border)] rounded-lg bg-[var(--color-bg-base)] text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] cursor-pointer min-w-[140px]"
            >
              {ontologyList.length === 0 && <option value="">无本体</option>}
              {ontologyList.map((o: any) => (
                <option key={o.id} value={o.id}>{o.name}</option>
              ))}
            </select>
            <Database size={12} className="absolute right-6 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)] pointer-events-none" />
            <ChevronDown size={12} className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)] pointer-events-none" />
          </div>

          {messages.length > 0 && (
            <button
              onClick={clearChat}
              className="text-xs text-[var(--color-text-tertiary)] hover:text-red-500 transition-colors px-2 py-1 rounded hover:bg-red-50"
            >
              清空
            </button>
          )}
        </div>
      </div>

      {/* 当前本体提示 */}
      {selectedOntology && (
        <div className="px-4 py-2 bg-blue-50/50 border-b border-blue-100 flex items-center gap-2">
          <Database size={12} className="text-blue-500" />
          <span className="text-xs text-blue-700">
            当前知识库：<strong>{selectedOntology.name}</strong>
            {selectedOntology.domain && <span className="text-blue-400 ml-1">· {selectedOntology.domain}</span>}
          </span>
        </div>
      )}

      {/* 消息区域 */}
      <div className="flex-1 overflow-auto px-4 py-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center anim-scale-in">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-50 to-indigo-50 flex items-center justify-center mb-5 shadow-sm">
              <Sparkles size={28} className="text-blue-500" />
            </div>
            <h3 className="text-lg font-semibold text-[var(--color-text-primary)] mb-2">智能问答中心</h3>
            <p className="text-sm text-[var(--color-text-tertiary)] max-w-md mb-8 leading-relaxed">
              选择模型和本体知识库，开始基于图谱的智能问答。支持实体查询、关系分析、规则推理等多种场景。
            </p>
            <div className="flex flex-wrap gap-2 justify-center max-w-lg">
              {suggestedQuestions.map(q => (
                <button key={q} onClick={() => setQuery(q)}
                  className="px-4 py-2 text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-border)] hover:border-blue-300 hover:bg-blue-50 text-[var(--color-text-secondary)] hover:text-blue-600 rounded-full transition-all duration-200 shadow-sm">
                  {q}
                </button>
              ))}
            </div>
            <div className="mt-6 flex items-center gap-4 text-xs text-[var(--color-text-tertiary)]">
              <span className="flex items-center gap-1"><Database size={10} />知识图谱</span>
              <span className="flex items-center gap-1"><Zap size={10} />LLM 驱动</span>
              <span className="flex items-center gap-1"><Lightbulb size={10} />智能搜索</span>
            </div>
          </div>
        ) : (
          messages.map(msg => (
            <div key={msg.id} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
              {msg.role === 'assistant' && (
                <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shrink-0 shadow-sm">
                  <Bot size={16} className="text-white" />
                </div>
              )}
              <div className={`max-w-[70%] rounded-2xl px-4 py-3 ${msg.role === 'user'
                  ? 'bg-[var(--color-primary)] text-white rounded-br-sm'
                  : 'bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-bl-sm shadow-sm'
                }`}>
                {msg.isLoading ? (
                  <div className="flex items-center gap-2 text-sm text-[var(--color-text-tertiary)] py-1">
                    <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
                    <span>思考中...</span>
                  </div>
                ) : (
                  <>
                    <p className="text-sm whitespace-pre-line leading-relaxed">{msg.content}</p>
                    {msg.sources && msg.sources.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-gray-100">
                        {msg.sources.map((s, i) => (
                          <Badge key={i} variant="secondary" className="text-[10px]">
                            {s.type === 'cypher' ? <Database size={8} className="mr-1" /> : <Lightbulb size={8} className="mr-1" />}
                            {s.title.slice(0, 25)}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
              {msg.role === 'user' && (
                <div className="w-8 h-8 rounded-xl bg-gray-100 flex items-center justify-center shrink-0">
                  <UserCircle size={16} className="text-gray-500" />
                </div>
              )}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* 输入区域 */}
      <div className="px-4 py-3 border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
        <div className="flex gap-2 max-w-4xl mx-auto">
          <Input
            placeholder={selectedOntologyId ? "输入您的问题..." : "请先选择一个本体知识库"}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSend()}
            disabled={!selectedOntologyId}
            className="flex-1"
          />
          <button
            onClick={handleSend}
            disabled={!query.trim() || isTyping || !selectedOntologyId}
            className="w-10 h-10 rounded-lg bg-[var(--color-primary)] text-white flex items-center justify-center transition-all duration-200 hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed btn-press"
          >
            <Send size={16} />
          </button>
        </div>
        <p className="text-[11px] text-[var(--color-text-tertiary)] mt-2 text-center">
          {llmModels.length > 0 ? `使用模型: ${llmModels.find((m: any) => m.id === selectedModelId)?.name || '默认'}` : '使用默认模型'}
          {selectedOntology && ` · 知识库: ${selectedOntology.name}`}
          {' · 无 API Key 时使用确定性搜索兜底'}
        </p>
      </div>
    </div>
  )
}
