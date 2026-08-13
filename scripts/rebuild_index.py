"""
scripts/rebuild_index.py — 就地重建 Milvus 向量索引（保留已入库实体）。

用于切换 MILVUS_INDEX_TYPE 后生效。索引仅在集合首次创建时建立，改常量
不会自动重建。

从项目根目录运行：
    HF_HUB_OFFLINE=1 python scripts/rebuild_index.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_pipeline import INDEX_TYPE, RAGPipeline


def main() -> None:
    rag = RAGPipeline()
    print(f"entities: {rag.count()}")
    print(f"rebuilding index as {INDEX_TYPE} ...")
    rag.rebuild_index()
    print("done")


if __name__ == "__main__":
    main()
