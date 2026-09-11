import { FormEvent, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

type Namespace = { id: string; name: string; revision: number }
type Source = { id: string; external_id: string; formatted_text: string; raw_asr: string; occurred_at?: string; app?: string; processing_status: string; eligible: boolean; source_version: number }
type SourceDetail = Source & { chunks: { id: string; view: string; text: string }[]; memories: { id: string; kind: string; subject: string; predicate: string; value: string; state: string }[] }
type Memory = { id: string; kind: string; subject: string; predicate: string; value: string; scope: string; state: string }
type Evidence = { id: string; external_id: string; text: string; occurred_at?: string }
type Clarification = {
  subject: string
  predicate: string
  old_value: string
  new_value: string
  old_source_id: string
  new_source_id: string
  temporal_gap_hours: number
  message: string
}
type Answer = { status: string; answer: string; draft_text?: string; evidence: Evidence[]; uncertainties: string[]; clarifications?: Clarification[] }

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '')
const DEMO_STORE_KEY = 'kivi-memory-workbench:v1'

type DemoStore = {
  namespaces: Namespace[]
  sources: SourceDetail[]
  memories: (Memory & { source_id?: string; supersedes_id?: string })[]
}

function loadDemoStore(): DemoStore {
  const stored = window.localStorage.getItem(DEMO_STORE_KEY)
  if (stored) {
    try {
      return JSON.parse(stored) as DemoStore
    } catch {
      const backupKey = `${DEMO_STORE_KEY}:corrupted:${Date.now()}`
      window.localStorage.setItem(backupKey, stored)
      console.warn(`Preserved corrupted demo storage under key "${backupKey}"`)
    }
  }
  const initial: DemoStore = {
    namespaces: [],
    sources: [],
    memories: [],
  }
  saveDemoStore(initial)
  return initial
}

function saveDemoStore(store: DemoStore) {
  window.localStorage.setItem(DEMO_STORE_KEY, JSON.stringify(store))
}

const STOPWORDS = new Set([
  'a', 'about', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
  'how', 'i', 'in', 'is', 'it', 'of', 'on', 'or', 'that', 'the', 'this',
  'to', 'was', 'what', 'when', 'where', 'which', 'who', 'why', 'will', 'with',
])

function words(value: string) {
  const raw = value.toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) || []
  return new Set(raw.filter(t => !STOPWORDS.has(t)))
}

function bestSources(store: DemoStore, namespaceId: string, question: string) {
  const query = words(question)
  if (query.size === 0) return []
  const suppressedSourceIds = new Set(
    store.memories.filter(m => m.state === 'suppressed').map(m => m.source_id)
  )
  return store.sources
    .filter(source => source.eligible && source.processing_status === 'ready' && source.id.startsWith(`${namespaceId}:`) && !suppressedSourceIds.has(source.id))
    .map(source => {
      const haystack = words(`${source.formatted_text} ${source.raw_asr}`)
      let score = 0
      query.forEach(term => { if (haystack.has(term)) score += 1 })
      return { source, score }
    })
    .sort((a, b) => b.score - a.score || (b.source.occurred_at || '').localeCompare(a.source.occurred_at || ''))
    .filter(item => item.score > 0)
    .slice(0, 3)
    .map(item => item.source)
}

function deriveMemories(namespaceId: string, source: SourceDetail): (Memory & { source_id: string })[] {
  const text = source.formatted_text || source.raw_asr
  const sentences = text.split(/[.!?]\s+/).map(item => item.trim()).filter(Boolean)
  const results: (Memory & { source_id: string })[] = []
  sentences.forEach((sentence, index) => {
    const lower = sentence.toLowerCase()
    if (/\b(prefer|like|want)\b/.test(lower)) {
      results.push({
        id: `${source.id}:mem:${index}`,
        kind: 'preference',
        subject: 'user',
        predicate: 'preference',
        value: sentence.replace(/^(?:i|we)\s+(?:prefer|like|want)\s+/i, '').replace(/[.!?]+$/, '').trim() || sentence,
        scope: 'general',
        state: 'active',
        source_id: source.id,
      })
    } else if (/\b(?:launches|launch|is scheduled|scheduled)\b/.test(lower)) {
      results.push({
        id: `${source.id}:mem:${index}`,
        kind: 'fact',
        subject: sentence.split(/\s+(?:launches|launch|is scheduled|scheduled)/i)[0]?.trim() || 'item',
        predicate: 'schedule',
        value: sentence.replace(/^.+?\s+(?:launches|launch|is scheduled|scheduled)(?:\s+on|\s+for)?\s+/i, '').replace(/[.!?]+$/, '').trim() || sentence,
        scope: 'general',
        state: 'active',
        source_id: source.id,
      })
    } else if (/\bis\b/.test(lower)) {
      const parts = sentence.split(/\s+is\s+/i)
      if (parts.length >= 2) {
        results.push({
          id: `${source.id}:mem:${index}`,
          kind: 'fact',
          subject: parts[0].trim(),
          predicate: 'is',
          value: parts.slice(1).join(' is ').replace(/[.!?]+$/, '').trim(),
          scope: 'general',
          state: 'active',
          source_id: source.id,
        })
      }
    }
  })
  return results
}

async function demoApi<T>(path: string, options?: RequestInit): Promise<T> {
  const store = loadDemoStore()
  const method = options?.method || 'GET'
  const payload = options?.body ? JSON.parse(String(options.body)) : {}
  const namespaceMatch = path.match(/^\/api\/namespaces\/([^/]+)/)
  const namespaceId = namespaceMatch ? decodeURIComponent(namespaceMatch[1]) : undefined

  if (path === '/api/namespaces' && method === 'GET') return store.namespaces as T
  if (path === '/api/namespaces' && method === 'POST') {
    const item = { id: `ns_${crypto.randomUUID()}`, name: (payload.name || '').trim() || 'My memory', revision: 1 }
    store.namespaces.push(item)
    saveDemoStore(store)
    return item as T
  }
  if (namespaceId && path.endsWith('/sources') && method === 'GET') {
    return store.sources.filter(source => source.id.startsWith(`${namespaceId}:`)) as T
  }
  if (namespaceId && path.endsWith('/memories') && method === 'GET') {
    return store.memories.filter(memory => memory.id.startsWith(`${namespaceId}:`)) as T
  }
  if (namespaceId && path.endsWith('/imports/validate') && method === 'POST') {
    const lines = String(payload.jsonl || '').split(/\r?\n/).filter((line: string) => line.trim())
    const errors: { line: number; message: string }[] = []
    lines.forEach((line: string, index: number) => {
      try {
        const record = JSON.parse(line)
        if (!record.id || !(record.raw_asr || record.formatted_text)) errors.push({ line: index + 1, message: 'Record needs id and transcript text.' })
      } catch {
        errors.push({ line: index + 1, message: 'Line is not valid JSON.' })
      }
    })
    return { valid_count: lines.length - errors.length, invalid_count: errors.length, errors } as T
  }
  if (namespaceId && path.endsWith('/imports') && method === 'POST') {
    const lines = String(payload.jsonl || '').split(/\r?\n/).filter((line: string) => line.trim())
    let accepted = 0
    let conflicts = 0
    lines.forEach((line: string) => {
      const record = JSON.parse(line)
      const srcId = `${namespaceId}:src:${record.id}`
      const existing = store.sources.find(item => item.id === srcId)
      if (existing && !payload.replace_conflicts) {
        conflicts += 1
        return
      }
      const source: SourceDetail = {
        id: srcId,
        external_id: record.id,
        raw_asr: record.raw_asr || '',
        formatted_text: record.formatted_text || record.raw_asr || '',
        occurred_at: record.occurred_at,
        app: record.app,
        processing_status: 'ready',
        eligible: true,
        source_version: existing ? existing.source_version + 1 : 1,
        chunks: [{ id: `${namespaceId}:chunk:${record.id}`, view: 'formatted', text: record.formatted_text || record.raw_asr || '' }],
        memories: [],
      }
      const derivedMemories = deriveMemories(namespaceId, source)
      source.memories = derivedMemories
      store.sources = store.sources.filter(item => item.id !== source.id).concat(source)
      store.memories = store.memories.filter(item => item.source_id !== source.id).concat(derivedMemories)
      accepted += 1
    })
    const ns = store.namespaces.find(item => item.id === namespaceId)
    if (ns && accepted) ns.revision += 1
    saveDemoStore(store)
    return { accepted, conflicts } as T
  }
  if (path === '/api/worker/drain' && method === 'POST') return { state: 'idle', processed: 0 } as T
  if (namespaceId && path.endsWith('/reset') && method === 'POST') {
    store.sources = store.sources.filter(item => !item.id.startsWith(`${namespaceId}:`))
    store.memories = store.memories.filter(item => !item.id.startsWith(`${namespaceId}:`))
    const ns = store.namespaces.find(item => item.id === namespaceId)
    if (ns) ns.revision += 1
    saveDemoStore(store)
    return { namespace: namespaceId, deleted_sources: 0, revision: ns?.revision || 1 } as T
  }
  if (namespaceId && path.endsWith('/ask') && method === 'POST') {
    if (!payload.question || !String(payload.question).trim()) {
      throw new Error('question must contain a non-whitespace character')
    }
    const qWords = words(payload.question)
    const activeMems = store.memories.filter(m => m.id.startsWith(`${namespaceId}:`) && m.state === 'active')
    const matchingCorrections = activeMems.filter(m => {
      if (!m.supersedes_id && !m.id.includes(':corr:')) return false
      const mTokens = words(`${m.subject} ${m.predicate} ${m.value}`)
      let match = false
      qWords.forEach(w => { if (mTokens.has(w)) match = true })
      return match
    })

    if (matchingCorrections.length > 0) {
      const corrText = matchingCorrections.map(m => `Based on your correction: ${m.subject} ${m.predicate} ${m.value}.`).join('\n')
      return {
        status: 'answered',
        answer: corrText,
        generation: 'controlled_memory',
        evidence: [],
        uncertainties: [],
      } as T
    }

    const evidence = bestSources(store, namespaceId, payload.question || '')
    if (!evidence.length) {
      return {
        status: 'insufficient_evidence',
        answer: "I couldn't find supporting information in this memory.",
        evidence: [],
        uncertainties: ['Import a relevant transcript or ask about something already in history.'],
      } as T
    }
    const cited = evidence.map((source, index) => `${index + 1}. ${source.formatted_text || source.raw_asr}`).join('\n')
    const draft = `Here is a grounded update based on the available transcript history:\n\n${cited}`
    return {
      status: 'answered_from_local_history',
      answer: payload.mode === 'draft' ? '' : `Based on the matching transcript history, the strongest evidence is:\n\n${cited}`,
      draft_text: payload.mode === 'draft' ? draft : undefined,
      evidence: evidence.map(source => ({ id: source.id, external_id: source.external_id, text: source.formatted_text || source.raw_asr, occurred_at: source.occurred_at })),
      uncertainties: [],
    } as T
  }
  if (namespaceId && path.includes('/sources/') && method === 'GET') {
    const sourceId = decodeURIComponent(path.split('/sources/')[1])
    const source = store.sources.find(item => item.id === sourceId)
    if (!source) throw new Error('Source was not found.')
    return source as T
  }
  if (namespaceId && path.includes('/memories/') && path.endsWith('/suppressions') && method === 'POST') {
    const memoryId = decodeURIComponent(path.split('/memories/')[1].split('/')[0])
    const memory = store.memories.find(item => item.id === memoryId)
    if (memory) memory.state = 'suppressed'
    const namespace = store.namespaces.find(item => item.id === namespaceId)
    if (namespace) namespace.revision += 1
    saveDemoStore(store)
    return { id: memoryId, state: 'suppressed', revision: namespace?.revision || 1 } as T
  }
  if (namespaceId && path.includes('/memories/') && path.endsWith('/corrections') && method === 'POST') {
    if (!payload.value || !String(payload.value).trim()) {
      throw new Error('value must contain a non-whitespace character')
    }
    const memoryId = decodeURIComponent(path.split('/memories/')[1].split('/')[0])
    const memory = store.memories.find(item => item.id === memoryId)
    if (!memory) throw new Error('Memory was not found.')
    if (memory.state !== 'active') throw new Error('Only active memories can be corrected.')
    memory.state = 'superseded'
    const cleanVal = String(payload.value).trim()
    const replacement = {
      id: `${memory.source_id || namespaceId}:corr:${Date.now()}`,
      kind: memory.kind,
      subject: memory.subject,
      predicate: memory.predicate,
      value: cleanVal,
      scope: memory.scope,
      state: 'active',
      source_id: memory.source_id,
      supersedes_id: memory.id,
    }
    store.memories.push(replacement)
    const namespace = store.namespaces.find(item => item.id === namespaceId)
    if (namespace) namespace.revision += 1
    saveDemoStore(store)
    return { id: replacement.id, state: 'active', revision: namespace?.revision || 1 } as T
  }
  if (namespaceId && path.includes('/sources/') && method === 'DELETE') {
    const sourceId = decodeURIComponent(path.split('/sources/')[1].split('?')[0])
    store.sources = store.sources.filter(item => item.id !== sourceId)
    store.memories = store.memories.filter(item => item.source_id !== sourceId)
    const namespace = store.namespaces.find(item => item.id === namespaceId)
    if (namespace) namespace.revision += 1
    saveDemoStore(store)
    return { deleted: sourceId, revision: namespace?.revision || 1 } as T
  }
  throw new Error('This action needs the FastAPI backend. Set VITE_API_BASE_URL for the full server workflow.')
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  if (!API_BASE_URL && window.location.hostname.endsWith('.vercel.app')) return demoApi<T>(path, options)
  const endpoint = `${API_BASE_URL}${path}`
  let response: Response
  try {
    response = await fetch(endpoint, { ...options, headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) } })
  } catch {
    if (!API_BASE_URL && !['127.0.0.1', 'localhost'].includes(window.location.hostname)) return demoApi<T>(path, options)
    throw new Error(`Cannot reach the Kivi backend at ${API_BASE_URL || 'same-origin /api'}. Check VITE_API_BASE_URL, HTTPS availability, and backend TRUSTED_ORIGINS.`)
  }
  const responseText = await response.text()
  if (response.ok && !responseText.trim()) throw new Error('The backend returned an empty response. Please try again.')
  let body: Record<string, unknown> = {}
  try {
    body = responseText ? JSON.parse(responseText) as Record<string, unknown> : {}
  } catch {
    if (!API_BASE_URL && /<!doctype|<html/i.test(responseText)) return demoApi<T>(path, options)
    throw new Error(`The server at ${response.url || endpoint} returned non-JSON content. Set VITE_API_BASE_URL to the FastAPI origin, without /api.`)
  }
  if (!response.ok) {
    const detail = body.detail as Record<string, unknown> | undefined
    throw new Error(String(body.message || detail?.message || `Backend request failed with HTTP ${response.status}.`))
  }
  return body as T
}

function App() {
  const [namespaces, setNamespaces] = useState<Namespace[]>([])
  const [namespace, setNamespace] = useState<Namespace | null>(null)
  const [view, setView] = useState<'ask' | 'history' | 'memory' | 'import'>('ask')
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [memories, setMemories] = useState<Memory[]>([])
  const [selected, setSelected] = useState<SourceDetail | null>(null)
  const [jsonl, setJsonl] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [backendStatus, setBackendStatus] = useState<'checking' | 'connected' | 'offline'>('checking')

  const activeNamespaceRef = useRef<string | null>(null)
  activeNamespaceRef.current = namespace?.id || null

  useEffect(() => {
    if (!API_BASE_URL) {
      setBackendStatus('offline')
      return
    }
    fetch(`${API_BASE_URL}/api/health`)
      .then(res => res.ok ? setBackendStatus('connected') : setBackendStatus('offline'))
      .catch(() => setBackendStatus('offline'))
  }, [])

  const refreshNamespaces = async () => {
    const all = await api<Namespace[]>('/api/namespaces')
    setNamespaces(all)
    try {
      const savedId = window.sessionStorage?.getItem('kivi_active_namespace_id')
      if (savedId) {
        const found = all.find(item => item.id === savedId)
        if (found) {
          setNamespace(found)
        } else if (savedId === 'demo') {
          try {
            const demo = await api<Namespace>('/api/namespaces', { method: 'POST', body: JSON.stringify({ name: 'demo' }) })
            setNamespaces(prev => [...prev.filter(n => n.id !== demo.id), demo])
            setNamespace(demo)
          } catch {
            window.sessionStorage?.removeItem('kivi_active_namespace_id')
          }
        } else {
          window.sessionStorage?.removeItem('kivi_active_namespace_id')
        }
      }
    } catch {
      // Storage access restricted
    }
  }

  const refreshData = async (current = namespace) => {
    if (!current) return
    const curId = encodeURIComponent(current.id)
    const [nextSources, nextMemories, nextNamespaces] = await Promise.all([
      api<Source[]>(`/api/namespaces/${curId}/sources`),
      api<Memory[]>(`/api/namespaces/${curId}/memories`),
      api<Namespace[]>('/api/namespaces'),
    ])
    if (activeNamespaceRef.current === current.id) {
      setSources(nextSources)
      setMemories(nextMemories)
      setNamespaces(nextNamespaces)
      setNamespace(nextNamespaces.find(item => item.id === current.id) || current)
    }
  }

  useEffect(() => {
    refreshNamespaces().catch(error => setNotice(error.message))
  }, [])

  useEffect(() => {
    let cancelled = false
    setAnswer(null)
    setSelected(null)
    setQuestion('')
    setJsonl('')
    setSources([])
    setMemories([])
    if (namespace) {
      const curId = encodeURIComponent(namespace.id)
      Promise.all([
        api<Source[]>(`/api/namespaces/${curId}/sources`),
        api<Memory[]>(`/api/namespaces/${curId}/memories`),
        api<Namespace[]>('/api/namespaces'),
      ]).then(async ([nextSources, nextMemories, nextNamespaces]) => {
        if (cancelled) return
        if ((namespace.name === 'demo' || namespace.id === 'demo') && nextSources.length === 0) {
          try {
            setNotice('Loading sample demo records...')
            await api(`/api/namespaces/${curId}/seed`, { method: 'POST' })
            await api('/api/worker/drain', { method: 'POST' })
            const [seededSources, seededMemories] = await Promise.all([
              api<Source[]>(`/api/namespaces/${curId}/sources`),
              api<Memory[]>(`/api/namespaces/${curId}/memories`),
            ])
            if (cancelled) return
            setSources(seededSources)
            setMemories(seededMemories)
            setNotice('')
            return
          } catch {
            // gracefully continue
          }
        }
        setSources(nextSources)
        setMemories(nextMemories)
        setNamespaces(nextNamespaces)
        setNamespace(nextNamespaces.find(item => item.id === namespace.id) || null)
      }).catch(error => {
        if (!cancelled) setNotice(error.message)
      })
    }
    return () => { cancelled = true }
  }, [namespace?.id])

  const createSpace = async () => {
    const customSpaces = namespaces.filter(n => n.name !== 'demo' && n.id !== 'demo')
    const used = new Set(namespaces.map(item => item.name.toLocaleLowerCase()))
    let number = Math.max(1, customSpaces.length + 1)
    let name = customSpaces.length ? `Memory ${number}` : 'My memory'
    while (used.has(name.toLocaleLowerCase())) {
      number += 1
      name = `Memory ${number}`
    }
    setBusy(true)
    try {
      const item = await api<Namespace>('/api/namespaces', { method: 'POST', body: JSON.stringify({ name }) })
      try {
        window.sessionStorage?.setItem('kivi_active_namespace_id', item.id)
      } catch {}
      setNamespaces(prev => [...prev.filter(n => n.id !== item.id), item])
      setNamespace(item)
      setNotice(`Memory space “${item.name}” created.`)
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const resetSpace = async () => {
    if (!namespace) return
    if (!window.confirm(`Reset "${namespace.name}"? This will delete all transcripts, chunks, embeddings, and memories in this workspace.`)) return
    setBusy(true)
    try {
      await api(`/api/namespaces/${encodeURIComponent(namespace.id)}/reset`, { method: 'POST' })
      await refreshData()
      setNotice(`Workspace “${namespace.name}” has been reset. All records cleared.`)
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const submitAsk = async (event: FormEvent, mode: 'answer' | 'draft' = 'answer') => {
    event.preventDefault()
    if (!namespace) return
    if (!question.trim()) {
      setNotice('Type a topic or question first, then click "Ask Kivi" or "Draft an update".')
      return
    }
    const reqNsId = namespace.id
    setBusy(true)
    setNotice('')
    try {
      const res = await api<Answer>(`/api/namespaces/${encodeURIComponent(reqNsId)}/ask`, {
        method: 'POST',
        body: JSON.stringify({ question, mode }),
      })
      if (activeNamespaceRef.current === reqNsId) {
        setAnswer(res)
      }
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const importData = async (event: FormEvent) => {
    event.preventDefault()
    if (!namespace || !jsonl.trim()) return
    const reqNsId = namespace.id
    setBusy(true)
    try {
      const encId = encodeURIComponent(reqNsId)
      const preview = await api<{ valid_count: number; invalid_count: number; errors: { line: number; message: string }[] }>(
        `/api/namespaces/${encId}/imports/validate`,
        { method: 'POST', body: JSON.stringify({ jsonl }) },
      )
      if (preview.invalid_count) throw new Error(`Line ${preview.errors[0]?.line || '?'} is invalid. Fix the JSONL before importing.`)
      if (!preview.valid_count) throw new Error('No valid records found. Review the JSONL fields.')
      const result = await api<{ accepted: number; conflicts: number }>(
        `/api/namespaces/${encId}/imports`,
        { method: 'POST', body: JSON.stringify({ jsonl }) },
      )
      await api('/api/worker/drain', { method: 'POST' })
      if (activeNamespaceRef.current === reqNsId) {
        await refreshData()
        if (result.accepted) setJsonl('')
        setNotice(result.conflicts ? `Processed ${result.accepted} records; ${result.conflicts} changed IDs need explicit replacement.` : `Imported and processed ${result.accepted} records.`)
      }
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const suppress = async (memory: Memory) => {
    if (!namespace) return
    setBusy(true)
    try {
      await api(
        `/api/namespaces/${encodeURIComponent(namespace.id)}/memories/${encodeURIComponent(memory.id)}/suppressions`,
        {
          method: 'POST',
          body: JSON.stringify({ expected_revision: namespace.revision, operation_id: `ui-${crypto.randomUUID()}` }),
        },
      )
      await refreshData()
      setNotice('This memory will no longer be used in answers.')
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const correct = async (memory: Memory) => {
    if (!namespace) return
    const value = window.prompt('What should Kivi remember instead?', memory.value)
    if (!value?.trim()) return
    setBusy(true)
    try {
      await api(
        `/api/namespaces/${encodeURIComponent(namespace.id)}/memories/${encodeURIComponent(memory.id)}/corrections`,
        {
          method: 'POST',
          body: JSON.stringify({ value: value.trim(), expected_revision: namespace.revision, operation_id: `ui-${crypto.randomUUID()}` }),
        },
      )
      await refreshData()
      setNotice('Memory corrected. Future answers use the new value.')
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const deleteSource = async (source: Source) => {
    if (!namespace || !window.confirm(`Delete ${source.external_id}? Kivi will no longer be able to use its text.`)) return
    setBusy(true)
    try {
      await api(
        `/api/namespaces/${encodeURIComponent(namespace.id)}/sources/${encodeURIComponent(source.id)}?expected_revision=${namespace.revision}&operation_id=ui-${crypto.randomUUID()}`,
        { method: 'DELETE' },
      )
      if (selected?.id === source.id) {
        setSelected(null)
      }
      await refreshData()
      setNotice('Source deleted and removed from future answers.')
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const inspect = async (source: Pick<Source, 'id'>) => {
    if (!namespace) return
    setBusy(true)
    try {
      setSelected(await api<SourceDetail>(`/api/namespaces/${encodeURIComponent(namespace.id)}/sources/${encodeURIComponent(source.id)}`))
    } catch (error) {
      setNotice((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!namespace) {
    const demoSpace = namespaces.find(n => n.name === 'demo' || n.id === 'demo')
    return (
      <main className="welcome">
        <div className="welcome-mark">kivi<span>MEMORY</span></div>
        <p className="eyebrow">PRIVATE TRANSCRIPT MEMORY</p>
        <h1>Give Kivi a memory.</h1>
        <p>Import transcript history, recover the context behind an idea, and keep every answer connected to the exact source that supported it.</p>
        <div className="welcome-actions">
          <button onClick={createSpace} disabled={busy}>
            {busy ? 'Creating workspace...' : 'Create a memory space'}
          </button>
          <button
              className="quiet welcome-demo-btn"
              disabled={busy}
              onClick={async () => {
                let space = namespaces.find(n => n.name === 'demo' || n.id === 'demo')
                if (!space) {
                  setBusy(true)
                  try {
                    space = await api<Namespace>('/api/namespaces', { method: 'POST', body: JSON.stringify({ name: 'demo' }) })
                    setNamespaces(prev => [...prev.filter(n => n.id !== space!.id), space!])
                  } catch (error) {
                    setNotice((error as Error).message)
                    setBusy(false)
                    return
                  }
                  setBusy(false)
                }
                try {
                  window.sessionStorage?.setItem('kivi_active_namespace_id', space.id)
                } catch {}
                setNamespace(space)
              }}
            >
              Explore demo memory
            </button>
          {namespaces.length > 0 && (
            <div className="welcome-select-row">
              <select
                defaultValue=""
                onChange={event => {
                  const chosen = namespaces.find(item => item.id === event.target.value)
                  if (chosen) {
                    try {
                      window.sessionStorage?.setItem('kivi_active_namespace_id', chosen.id)
                    } catch {}
                    setNamespace(chosen)
                  }
                }}
              >
                <option value="" disabled>Choose an existing memory space...</option>
                {namespaces.map(item => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            </div>
          )}
        </div>
        <small>{API_BASE_URL ? 'Connected to the FastAPI memory backend.' : 'Vercel demo mode stores this workspace in your browser. Connect VITE_API_BASE_URL for the full backend workflow.'}</small>
        {notice && <p className="notice">{notice}</p>}
      </main>
    )
  }

  const readySources = sources.filter(source => source.eligible && source.processing_status === 'ready').length
  const activeMemories = memories.filter(memory => memory.state === 'active').length

  return (
    <div className="app">
      <aside>
        <div className="brand">kivi <small>MEMORY</small></div>
        <p className="namespace">{namespace.name}</p>
        <nav>
          {([['ask', 'Hey Kivi'], ['history', 'History'], ['memory', 'Memory'], ['import', 'Import']] as const).map(([id, label]) => (
            <button key={id} className={view === id ? 'active' : ''} onClick={() => { setView(id); setSelected(null) }}>{label}</button>
          ))}
        </nav>
        <div className="workspace-health">
          <span className={`health-dot ${backendStatus}`} />
          {backendStatus === 'connected' ? 'Backend connected' : backendStatus === 'checking' ? 'Checking backend...' : 'Browser workspace (demo mode)'}
          <div>{readySources} sources · {activeMemories} active memories</div>
        </div>
        <div className="space-controls">
          <label>
            WORKSPACE
            <select
              value={namespace.id}
              onChange={event => {
                const chosen = namespaces.find(item => item.id === event.target.value) || null
                if (chosen) {
                  try {
                    window.sessionStorage?.setItem('kivi_active_namespace_id', chosen.id)
                  } catch {}
                } else {
                  try {
                    window.sessionStorage?.removeItem('kivi_active_namespace_id')
                  } catch {}
                }
                setNamespace(chosen)
              }}
            >
              <option value="">-- Close workspace --</option>
                {namespaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <button className="quiet" onClick={createSpace}>+ New space</button>
          <button
            className="quiet"
            onClick={() => {
              try {
                window.sessionStorage?.removeItem('kivi_active_namespace_id')
              } catch {}
              setNamespace(null)
            }}
          >
            ← Welcome page
          </button>
          <button className="quiet destructive-action" onClick={resetSpace}>Reset this space</button>
        </div>
      </aside>
      <main className="content">
        {notice && <div className="notice">{notice}</div>}
        {view === 'ask' && <Ask question={question} setQuestion={setQuestion} answer={answer} busy={busy} submit={submitAsk} sourceCount={readySources} memoryCount={activeMemories} inspect={inspect} />}
        {view === 'history' && <History sources={sources} busy={busy} deleteSource={deleteSource} inspect={inspect} />}
        {view === 'memory' && <MemoryList memories={memories} busy={busy} suppress={suppress} correct={correct} />}
        {view === 'import' && <Import jsonl={jsonl} setJsonl={setJsonl} busy={busy} submit={importData} />}
      </main>
      {selected && <SourceInspector source={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

function renderAnswerText(
  text: string,
  evidence: Evidence[],
  inspect: (source: Pick<Source, 'id'>) => void
) {
  const pattern = /\[source:([^\]]+)\]/g
  const parts: (string | React.ReactNode)[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    const inner = match[1]
    const ids = inner.split(/[,;\s]+/).map(s => s.replace(/^source:/, '').trim()).filter(Boolean)

    ids.forEach((sourceId, i) => {
      const evidenceIndex = evidence.findIndex(e => e.id === sourceId)
      const num = evidenceIndex >= 0 ? evidenceIndex + 1 : null
      const sourceItem = evidenceIndex >= 0 ? evidence[evidenceIndex] : { id: sourceId }
      const externalLabel = 'external_id' in sourceItem && sourceItem.external_id ? sourceItem.external_id : sourceId
      const titleText = evidenceIndex >= 0
        ? `Source ${num}: ${externalLabel}`
        : `Source: ${sourceId}`

      parts.push(
        <button
          key={`cite-${match!.index}-${sourceId}-${i}`}
          type="button"
          className="citation-pill"
          onClick={() => inspect(sourceItem)}
          title={titleText}
        >
          {num ?? 'source'}
        </button>
      )
    })
    lastIndex = pattern.lastIndex
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }

  return parts
}

function Ask({ question, setQuestion, answer, busy, submit, sourceCount, memoryCount, inspect }: { question: string; setQuestion: (value: string) => void; answer: Answer | null; busy: boolean; submit: (event: FormEvent, mode?: 'answer' | 'draft') => void; sourceCount: number; memoryCount: number; inspect: (source: Pick<Source, 'id'>) => void }) {
  return (
    <>
      <header>
        <p className="eyebrow">INTENTIONAL MEMORY</p>
        <h1>Pick up where<br />you left off.</h1>
        <p>Find something you said. See what changed. Prepare the next response with the record beside you.</p>
      </header>
      <form className="askbox" onSubmit={event => submit(event)}>
        <textarea
          value={question}
          onChange={event => setQuestion(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              if (!busy && question.trim()) {
                submit(event)
              }
            }
          }}
          placeholder="Ask about something you dictated... (Press Enter to ask, Shift+Enter for new line)"
        />
        <div>
          <small>Your history stays within this workspace.</small>
          <span>
            <button type="submit" disabled={busy}>{busy ? 'Looking...' : 'Ask Kivi'}</button>
            <button type="button" className="secondary" disabled={busy} onClick={event => submit(event, 'draft')}>Draft an update</button>
          </span>
        </div>
      </form>
      {!answer && (
        <>
          <section className="starter">
            <p className="eyebrow">A PLACE TO START</p>
            <button onClick={() => setQuestion('What changed in the latest project update?')}>What changed about the launch?</button>
            <button onClick={() => setQuestion('What preferences have I mentioned?')}>What preferences have I mentioned?</button>
          </section>
          <section className="memory-stats">
            <article><strong>{sourceCount}</strong><span>searchable sources</span></article>
            <article><strong>{memoryCount}</strong><span>active memory candidates</span></article>
          </section>
        </>
      )}
      {answer && (
        <section className="answer">
          <p className="eyebrow">{answer.status.replaceAll('_', ' ')}</p>
          <h2>{answer.draft_text ? 'Draft an update' : 'A useful answer, with a path back to what you said.'}</h2>
          <div className="answer-card">
            <div className="answer-text">{renderAnswerText(answer.draft_text || answer.answer, answer.evidence, inspect)}</div>
          </div>
          {answer.clarifications && answer.clarifications.length > 0 && (
            <div className="clarification-card" style={{ background: 'rgba(234, 179, 8, 0.08)', border: '1px solid rgba(234, 179, 8, 0.3)', borderRadius: '8px', padding: '1rem', margin: '1rem 0' }}>
              <strong style={{ color: '#eab308', display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                <span>⚠️</span> Direct Contradiction Detected
              </strong>
              {answer.clarifications.map((c, i) => (
                <div key={i} style={{ marginTop: '0.5rem' }}>
                  <p style={{ margin: 0 }}>{c.message}</p>
                  <small style={{ opacity: 0.7, fontSize: '0.8rem' }}>Recorded within {c.temporal_gap_hours} hours of each other.</small>
                </div>
              ))}
            </div>
          )}
          {answer.evidence.length > 0 && (
            <>
              <h3>Sources behind this answer</h3>
              <div className="sources">
                {answer.evidence.map((source, index) => (
                  <button type="button" className="source-evidence" disabled={busy} onClick={() => inspect(source)} key={source.id}>
                    <strong>
                      <span className="source-badge">{index + 1}</span>
                      {source.external_id || source.id}
                    </strong>
                    <p>{source.text}</p>
                    <small>Inspect source →</small>
                  </button>
                ))}
              </div>
            </>
          )}
          {answer.uncertainties.map(item => <p className="uncertainty" key={item}>{item}</p>)}
        </section>
      )}
    </>
  )
}

function History({ sources, deleteSource, inspect, busy }: { sources: Source[]; deleteSource: (source: Source) => void; inspect: (source: Source) => void; busy: boolean }) {
  return (
    <>
      <header>
        <p className="eyebrow">HISTORY</p>
        <h1>What Kivi can look back on.</h1>
        <p>Each entry keeps the raw dictation and its cleaned transcript together.</p>
      </header>
      {sources.length ? (
        <div className="cards">
          {sources.map(source => {
            const visibleStatus = source.eligible ? source.processing_status : 'replaced'
            return (
              <article className="card" key={source.id}>
                <div><strong>{source.external_id} · v{source.source_version}</strong><span className={`status ${visibleStatus}`}>{visibleStatus}</span></div>
                <p>{source.formatted_text || source.raw_asr || 'Empty transcript record'}</p>
                <small>{source.occurred_at ? new Date(source.occurred_at).toLocaleString() : 'Time unknown'}{source.app ? ` · ${source.app}` : ''}</small>
                <footer>
                  <button className="quiet" disabled={busy} onClick={() => inspect(source)}>Inspect source</button>
                  <button className="quiet danger" disabled={busy} onClick={() => deleteSource(source)}>Delete</button>
                </footer>
              </article>
            )
          })}
        </div>
      ) : <Empty text="Import a transcript history to see it here." />}
    </>
  )
}

function MemoryList({ memories, suppress, correct, busy }: { memories: Memory[]; suppress: (memory: Memory) => void; correct: (memory: Memory) => void; busy: boolean }) {
  return (
    <>
      <header>
        <p className="eyebrow">SCOPED MEMORY</p>
        <h1>Facts and preferences Kivi learned.</h1>
        <p>Each item remains tied to the source that supported it.</p>
      </header>
      {memories.length ? (
        <div className="cards">
          {memories.map(memory => (
            <article className="card" key={memory.id}>
              <div><strong>{memory.kind}</strong><span className={`status ${memory.state}`}>{memory.state}</span></div>
              <p>{memory.subject} · {memory.predicate} · <b>{memory.value}</b></p>
              <small>Scope: {memory.scope}</small>
              {memory.state === 'active' && (
                <footer>
                  <button className="quiet" disabled={busy} onClick={() => correct(memory)}>Correct</button>
                  <button className="quiet danger" disabled={busy} onClick={() => suppress(memory)}>Stop using</button>
                </footer>
              )}
            </article>
          ))}
        </div>
      ) : <Empty text="Kivi has not promoted any conservative memory candidates yet. Sources are still searchable." />}
    </>
  )
}

function Import({ jsonl, setJsonl, busy, submit }: { jsonl: string; setJsonl: (value: string) => void; busy: boolean; submit: (event: FormEvent) => void }) {
  return (
    <>
      <header>
        <p className="eyebrow">IMPORT</p>
        <h1>Bring in a transcript history.</h1>
        <p>Paste UTF-8 JSONL using the documented v1 record format. Source text is retained exactly.</p>
      </header>
      <form className="import" onSubmit={submit}>
        <textarea value={jsonl} onChange={event => setJsonl(event.target.value)} placeholder={'{"schema_version":1,"id":"note-001","raw_asr":"lantern launch monday","formatted_text":"Lantern launches Monday."}'} />
        <button disabled={busy}>{busy ? 'Importing...' : 'Validate, import, and process'}</button>
      </form>
    </>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>
}

function SourceInspector({ source, onClose }: { source: SourceDetail; onClose: () => void }) {
  return (
    <aside className="inspector" aria-label="Source inspector">
      <button className="close" onClick={onClose}>×</button>
      <p className="eyebrow">SOURCE INSPECTOR</p>
      <h2>{source.external_id}</h2>
      <p className="source-meta">{source.occurred_at ? new Date(source.occurred_at).toLocaleString() : 'Time unknown'}{source.app ? ` · ${source.app}` : ''}</p>
      <h3>Formatted transcript</h3>
      <p>{source.formatted_text || 'No formatted transcript supplied.'}</p>
      <h3>Raw dictation</h3>
      <p>{source.raw_asr || 'No raw ASR supplied.'}</p>
      <h3>Indexed passages</h3>
      {source.chunks.length ? source.chunks.map(chunk => <p className="chunk" key={chunk.id}>{chunk.text}</p>) : <p>No passages indexed yet.</p>}
      <h3>Memories derived here</h3>
      {source.memories.length ? source.memories.map(memory => <p className="chunk" key={memory.id}>{memory.subject} · {memory.predicate} · {memory.value} <em>{memory.state}</em></p>) : <p>No conservative memory candidates were promoted.</p>}
    </aside>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
