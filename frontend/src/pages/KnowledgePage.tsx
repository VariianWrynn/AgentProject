import { KnowledgePanel } from '../components/KnowledgePanel'
import styles from './KnowledgePage.module.css'

export function KnowledgePage() {
  return (
    <div className={styles.page}>
      <div className={styles.topBar}>
        <a href="/" className={styles.backLink}>← 返回研究</a>
      </div>
      <h1 className={styles.title}>RAG 知识库管理</h1>
      <p className={styles.sub}>管理用于深度研究的能源行业文档知识库</p>
      <KnowledgePanel />
    </div>
  )
}
