import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { HealthStatus } from '../types/api'
import styles from './HealthBadge.module.css'

const SERVICE_KEYS: (keyof HealthStatus)[] = ['api', 'milvus', 'redis', 'mcp_server']
const SERVICE_LABELS: Record<string, string> = {
  api: 'API',
  milvus: 'Milvus',
  redis: 'Redis',
  mcp_server: 'MCP',
}

function statusDot(val?: string): string {
  if (!val) return '⚫'
  if (val === 'ok') return '🟢'
  // Timeout errors are temporary — show yellow warning instead of red error
  if (val.includes('timeout') || val.includes('timed out')) return '🟡'
  return '🔴'
}

export function HealthBadge() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [error, setError] = useState(false)

  const fetchHealth = async () => {
    try {
      const res = await api.getHealth()
      setHealth(res.data)
      setError(false)
    } catch {
      setError(true)
    }
  }

  useEffect(() => {
    fetchHealth()
    const interval = setInterval(fetchHealth, 30000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className={styles.badge}>
      {error ? (
        <span className={styles.error}>🔴 Backend unreachable</span>
      ) : health ? (
        SERVICE_KEYS.map((key) => (
          <span key={key} className={styles.service} title={`${SERVICE_LABELS[key]}: ${health[key] ?? 'unknown'}`}>
            {statusDot(health[key] as string)} {SERVICE_LABELS[key]}
          </span>
        ))
      ) : (
        <span className={styles.loading}>⚫ Checking...</span>
      )}
    </div>
  )
}
