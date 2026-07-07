import { useState, useRef, useEffect } from 'react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { LoadingState, EmptyState } from '@/components/ui/LoadingState'
import { Send, Bot, UserCircle, Sparkles, Database, FileText, Zap, Lightbulb } from 'lucide-react'
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

export default function RagQATab({ ontologyId }: { ontologyId: string }) {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [isTyping, setIsTyping] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const suggestedQuestions = [
    '华融控股旗下有哪些子公司？',
    '鼎盛实业的实控人是谁？',
    '哪些企业之间存在担保关系？',
    '赵强控制了哪些企业？',
    '检测循环担保风险',
  ]

  const handleSend = async () => {
    if (!query.trim() || isTyping) return
    const userMsg: Message = { id: Date.now().toString(), role: 'user', content: query.trim() }
    setMessages(prev => [...prev, userMsg])
    const currentQuery = query.trim()
    setQuery('')
    setIsTyping(true)

    const loadingId = (Date.now() + 1).toString()
    setMessages(prev => [...prev, { id: loadingId, role: 'assistant', content: '', isLoading: true }])

    try {
      const r = await axios.post(`/api/v2/ontologies/${ontologyId}/graph/ask`, { question: currentQuery }, {
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
        answer = '未找到与该问题直接相关的结果。您可以尝试：\n• 使用更具体的企业名称\n• 询问已知的实体或关系\n• 检查关键词拼写'
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

  return (
    <div className="flex flex-col h-[calc(100vh-240px)] anim-fade-in">
      {/* Messages */}
      <div className="flex-1 overflow-auto space-y-4 pr-2 pb-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center anim-scale-in">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-50 to-indigo-50 flex items-center justify-center mb-5 shadow-sm">
              <Sparkles size={28} className="text-blue-500" />
            </div>
            <h3 className="text-lg font-semibold text-[var(--color-text-primary)] mb-2">智能问答</h3>
            <p className="text-sm text-[var(--color-text-tertiary)] max-w-sm mb-8 leading-relaxed">
              基于知识图谱 + LLM，回答关于企业、关系、风险的问题
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
              <span className="flex items-center gap-1"><Zap size={10} />DeepSeek</span>
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
                            {s.type === 'cypher' ? <Database size={8} className="mr-1" /> : <FileText size={8} className="mr-1" />}
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

      {/* Input */}
      <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
        <div className="flex gap-2">
          <Input placeholder="输入您的问题，例如：华融控股的子公司有哪些？" value={query}
            onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleSend()} className="flex-1" />
          <Button onClick={handleSend} disabled={!query.trim() || isTyping}>
            <Send size={16} />
          </Button>
        </div>
        <p className="text-xs text-[var(--color-text-tertiary)] mt-2">基于知识图谱 + DeepSeek LLM，无 API Key 时使用确定性搜索兜底</p>
      </div>
    </div>
  )
}
