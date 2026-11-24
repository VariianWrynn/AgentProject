import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import type { Root, RootContent, PhrasingContent } from 'mdast'
import type { Reference } from '../types/api'

function normalizeCitations(tree: Root, references: Reference[]) {
  const known = new Set(references.map((reference) => reference.label))
  const cited = new Set<string>()
  function transform(parent: { children: RootContent[] }) {
    parent.children = parent.children.flatMap((node): RootContent[] => {
      if (node.type === 'link' || node.type === 'linkReference') {
        const text = node.children.map((child) => child.type === 'text' ? child.value : '').join('')
        const label = text.match(/^\[?([ED]\d+)\]?$/)?.[1]
          ?? (node.type === 'link' ? node.url.match(/^#(?:reference-|evidence-)([ED]\d+)$/)?.[1] : undefined)
        if (label && known.has(label)) {
          cited.add(label)
          return [{ type: 'link', url: `#reference-${label}`, children: [{ type: 'text', value: `[${label}]` }] }]
        }
        return [node]
      }
      if (node.type === 'text') {
        const result: PhrasingContent[] = []
        let start = 0
        for (const match of node.value.matchAll(/\[([ED]\d+)\]/g)) {
          const label = match[1]
          if (!known.has(label)) continue
          cited.add(label)
          if (match.index > start) result.push({ type: 'text', value: node.value.slice(start, match.index) })
          result.push({ type: 'link', url: `#reference-${label}`, children: [{ type: 'text', value: `[${label}]` }] })
          start = match.index + match[0].length
        }
        if (start === 0) return [node]
        if (start < node.value.length) result.push({ type: 'text', value: node.value.slice(start) })
        return result
      }
      if ('children' in node) transform(node)
      return [node]
    })
  }
  transform(tree)
  return cited
}

export function citationPlugin(references: Reference[]) {
  return () => (tree: Root) => { normalizeCitations(tree, references) }
}

export function citedLabels(markdown: string, references: Reference[]) {
  const tree = unified().use(remarkParse).use(remarkGfm).parse(markdown)
  return normalizeCitations(tree, references)
}
