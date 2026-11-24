import { useEffect, useState } from 'react'
import { KnowledgePage } from './pages/KnowledgePage'
import { ResearchPage } from './pages/ResearchPage'
import { ObservatoryPage } from './pages/ObservatoryPage'
import './index.css'

// Minimal hash-based router — no react-router dependency needed for v1
function getRoute(): string {
  const hash = window.location.hash
  if (hash.startsWith('#/knowledge')) return 'knowledge'
  if (hash.startsWith('#/observatory')) return 'observatory'
  return 'research'
}

function App() {
  const [route, setRoute] = useState(getRoute())

  useEffect(() => {
    const handler = () => {
      if (window.location.hash.startsWith('#reference-') || window.location.hash.startsWith('#evidence-')) return
      setRoute(getRoute())
    }
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  // Override <a href="/knowledge"> clicks to use hash routing
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLAnchorElement
      if (target.tagName === 'A') {
        const href = target.getAttribute('href')
        if (href === '/knowledge') {
          e.preventDefault()
          window.location.hash = '#/knowledge'
        } else if (href === '/') {
          e.preventDefault()
          window.location.hash = ''
        }
      }
    }
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [])

  return route === 'knowledge' ? <KnowledgePage /> : route === 'observatory' ? <ObservatoryPage /> : <ResearchPage />
}

export default App
