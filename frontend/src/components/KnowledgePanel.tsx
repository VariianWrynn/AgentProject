import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { KnowledgeSource } from '../types/api'
import styles from './KnowledgePanel.module.css'

export function KnowledgePanel() {
  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [ingesting, setIngesting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadSources = async () => {
    try {
      setLoading(true)
      const res = await api.getSources()
      // res.data is SourceList: {sources: [{source: str}], total: int}
      setSources(res.data.sources ?? [])
      setError('')
    } catch (e) {
      setError('加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSources()
  }, [])

  const handleDelete = async (sourceName: string) => {
    if (!confirm(`确认删除 "${sourceName}"？`)) return
    setDeleting(sourceName)
    try {
      await api.deleteSource(sourceName)
      await loadSources()
    } catch {
      alert('删除失败')
    } finally {
      setDeleting(null)
    }
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const content = await file.text()
    const sourceName = file.name.replace(/\.[^.]+$/, '')   // strip extension
    setIngesting(true)
    try {
      await api.ingestSource(sourceName, content)
      await loadSources()
      if (fileInputRef.current) fileInputRef.current.value = ''
    } catch {
      alert('上传失败')
    } finally {
      setIngesting(false)
    }
  }

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h2 className={styles.title}>知识库文档</h2>
        <button
          className={styles.uploadBtn}
          onClick={() => fileInputRef.current?.click()}
          disabled={ingesting}
        >
          {ingesting ? '上传中...' : '+ 上传文档'}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.md"
          style={{ display: 'none' }}
          onChange={handleFileUpload}
        />
      </div>

      {error && <div className={styles.error}>{error}</div>}

      {loading ? (
        <div className={styles.loading}>加载中...</div>
      ) : sources.length === 0 ? (
        <div className={styles.empty}>知识库为空</div>
      ) : (
        <ul className={styles.list}>
          {sources.map((src) => (
            <li key={src.source} className={styles.item}>
              <span className={styles.sourceName}>📄 {src.source}</span>
              <button
                className={styles.deleteBtn}
                onClick={() => handleDelete(src.source)}
                disabled={deleting === src.source}
              >
                {deleting === src.source ? '...' : '删除'}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className={styles.footer}>
        共 {sources.length} 个文档
        <button className={styles.refreshBtn} onClick={loadSources}>刷新</button>
      </div>
    </div>
  )
}
