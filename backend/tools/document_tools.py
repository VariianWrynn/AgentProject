"""Document retrieval tools operating on the MCP-owned retrieval instance."""
import json

def doc_summary(rag, source: str) -> str:
    results = rag.collection.query(
        expr=f"source == {json.dumps(source, ensure_ascii=False)}",
        output_fields=["chunk_id", "content"], limit=1000)
    if not results:
        return f"No document found with source name '{source}' in the knowledge base."
    results.sort(key=lambda item: item["chunk_id"])
    return "\n\n".join(item["content"] for item in results)[:6000]
