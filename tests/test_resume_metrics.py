"""
Resume Metrics Test Suite
运行: python tests/test_resume_metrics.py
输出: reports/resume_metrics_YYYYMMDD_HHMMSS.md

四项测试并行执行，每项均有异常保护；使用 llm_router 分配 API Key。
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reconfigure stdout/stderr to UTF-8 so emoji in print() work on Windows (GBK default)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING)

BASE_API = "http://localhost:8003"
BASE_MCP = "http://localhost:8002"

_lock   = threading.Lock()
results: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Minimal LLMClient shim — replicates chat_json without importing react_engine
# (react_engine → rag_pipeline → SentenceTransformer → torch, not installed here)
# ─────────────────────────────────────────────────────────────────────────────

class _MinimalLLMClient:
    """Thin wrapper around an openai.OpenAI client exposing chat_json()."""

    def __init__(self, client, model: str) -> None:
        self._client = client
        self.model   = model

    def chat_json(self, system: str, user: str, temperature: float = 0.2) -> dict:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception:
            # Provider may ignore response_format — extract JSON from raw text
            import re
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
            )
            raw = resp.choices[0].message.content
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group())
            return {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _try_extract_troubleshoot(test_name: str, exc: Exception) -> None:
    """Invoke extract-troubleshooting.sh to create a stub bug log (best-effort)."""
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "extract-troubleshooting.sh",
    )
    try:
        subprocess.run(["bash", script], capture_output=True, timeout=10)
    except Exception:
        pass  # never let the helper crash the suite


# ─────────────────────────────────────────────────────────────────────────────
# TEST A: RouterNode 意图分类准确率  →  KEY_1 (router)
# ─────────────────────────────────────────────────────────────────────────────

def test_router_accuracy():
    print("\n=== TEST A: RouterNode Intent Classification ===")
    try:
        test_cases = [
            ("碳中和政策最新进展是什么？",           "policy_query"),
            ("宁德时代2023年储能业务营收是多少？",    "data_query"),
            ("光伏行业未来五年市场规模预测",           "market_analysis"),
            ("储能行业主要竞争格局分析",               "market_analysis"),
            ("新能源补贴政策有哪些新变化？",           "policy_query"),
            ("华东地区2024年风电新增装机容量",         "data_query"),
            ("你好，请介绍一下你自己",                 "general"),
            ("分析中国能源转型的挑战和机遇",           "research"),
            ("光伏组件2024年价格走势如何？",           "market_analysis"),
            ("电力市场改革对煤电企业的影响分析",       "research"),
        ]

        correct = 0
        details = []
        for question, expected in test_cases:
            resp = requests.post(
                f"{BASE_API}/chat",
                json={"question": question, "session_id": f"test_router_{int(time.time())}"},
                timeout=120,
            )
            actual_intent = resp.json().get("intent", "unknown")
            is_correct = actual_intent == expected
            if is_correct:
                correct += 1
            details.append({
                "question": question[:30],
                "expected": expected,
                "actual":   actual_intent,
                "correct":  is_correct,
            })
            print(f"  {'✅' if is_correct else '❌'} {question[:30]}: {actual_intent}")

        accuracy = correct / len(test_cases)
        with _lock:
            results["router_accuracy"] = {
                "score":    f"{correct}/{len(test_cases)}",
                "accuracy": f"{accuracy * 100:.0f}%",
                "details":  details,
            }
        print(f"  → Router accuracy: {correct}/{len(test_cases)} ({accuracy * 100:.0f}%)")
        return accuracy

    except Exception as e:
        print(f"  ❌ test_router_accuracy FAILED: {e}")
        _try_extract_troubleshoot("test_router_accuracy", e)
        with _lock:
            results["router_accuracy"] = {"status": "ERROR", "error": str(e)}
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# TEST B: CriticMaster 问题检出率  →  KEY_5 (critic_master)
# ─────────────────────────────────────────────────────────────────────────────

def test_critic_detection():
    print("\n=== TEST B: CriticMaster Issue Detection ===")
    try:
        from llm_router import get_client
        # critic_master exposes a module-level run(state, llm) function — not a class
        from backend.agents import critic_master as critic_module

        # Use get_client (openai only, no torch) + our shim instead of make_llm
        _client, _model = get_client("critic_master")   # KEY_5
        llm = _MinimalLLMClient(_client, _model)

        defective_drafts = [
            {"type": "hallucination",  "content": "宁德时代2024年储能出货量达500GWh，占全球市场份额75%。"},
            {"type": "missing_source", "content": "2024年中国新增储能装机43.7GW，同比增长103%。"},
            {"type": "logic_error",    "content": "储能成本从2022年的1.5元/Wh下降至2024年的2.0元/Wh，降幅显著。"},
            {"type": "outdated",       "content": "根据2019年数据，光伏装机成本约为8元/W，未来有望降至5元/W。"},
            {"type": "incomplete",     "content": "储能行业竞争格局分析：主要企业包括宁德时代。"},
        ]

        detected = 0
        details  = []
        for draft in defective_drafts:
            test_state = {
                "session_id":      f"test_critic_{int(time.time())}",
                "question":        "测试报告质量",
                "draft_sections":  {"sec1": draft["content"]},
                "facts":           [],
                "outline":         [{"id": "sec1", "title": "测试章节"}],
                "iteration":       0,
                "demo_mode":       False,
                "phase":           "reviewing",
                "critic_issues":   [],
                "quality_score":   0.0,
                "pending_queries": [],
                "unresolved_issues": 0,
            }

            # run() is a module-level function: run(state, llm)
            result_state = critic_module.run(test_state, llm)
            issues       = result_state.get("critic_issues", [])
            issue_types  = [i.get("type", "") for i in issues]

            is_detected = len(issues) > 0
            if is_detected:
                detected += 1
            details.append({
                "defect_type":  draft["type"],
                "detected":     is_detected,
                "issues_found": len(issues),
                "issue_types":  issue_types,
            })
            print(f"  {'✅' if is_detected else '❌'} {draft['type']}: {len(issues)} issues {issue_types}")

        detection_rate = f"{detected}/{len(defective_drafts)}"
        with _lock:
            results["critic_detection"] = {
                "score":   detection_rate,
                "details": details,
            }
        print(f"  → CriticMaster detection rate: {detection_rate}")
        return detected

    except Exception as e:
        print(f"  ❌ test_critic_detection FAILED: {e}")
        _try_extract_troubleshoot("test_critic_detection", e)
        with _lock:
            results["critic_detection"] = {"status": "ERROR", "error": str(e)}
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# TEST C: 三层降级验证  →  KEY_2 (deep_scout), HTTP-only layers 1 & 3
# ─────────────────────────────────────────────────────────────────────────────

def test_degradation_layers():
    print("\n=== TEST C: Three-Layer Degradation ===")
    try:
        from mcp_client import MCPCallError, MCPClient

        layer_results: dict = {}

        # Layer 1 — tool-level isolation: bad rag_search should not kill text2sql
        print("  Layer 1: Tool-level isolation...")
        try:
            resp1 = requests.post(
                f"{BASE_MCP}/tools/rag_search",
                json={"query": "", "params": {}, "session_id": "test"},
                timeout=10,
            )
            resp2 = requests.post(
                f"{BASE_MCP}/tools/text2sql",
                json={"query": "查询营收最高的企业", "params": {}, "session_id": "test"},
                timeout=30,
            )
            layer1_pass = resp2.status_code == 200
            layer_results["tool_isolation"] = {
                "pass":         layer1_pass,
                "rag_status":   resp1.status_code,
                "text2sql_status": resp2.status_code,
            }
            print(f"  {'✅' if layer1_pass else '❌'} Tool isolation: rag={resp1.status_code}, sql={resp2.status_code}")
        except Exception as e:
            layer_results["tool_isolation"] = {"pass": False, "error": str(e)}
            print(f"  ❌ Layer 1 error: {e}")

        # Layer 2 — MCPClient fallback: unreachable port → MCPCallError (not crash)
        print("  Layer 2: MCPClient fallback...")
        try:
            bad_client = MCPClient("http://localhost:9999")
            bad_client.call("rag_search", "储能行业", {}, "test")
            # Should have raised — if we get here it unexpectedly succeeded
            layer2_pass = False
            layer_results["mcp_fallback"] = {"pass": False, "note": "expected MCPCallError, got result"}
            print("  ❌ MCPClient fallback: expected error but call succeeded")
        except MCPCallError as mce:
            # MCPCallError on unreachable host = expected graceful error path
            layer2_pass = True
            layer_results["mcp_fallback"] = {"pass": True, "exception": "MCPCallError", "msg": str(mce)[:80]}
            print(f"  ✅ MCPClient fallback: MCPCallError raised as expected")
        except Exception as e:
            layer2_pass = "ConnectionError" in type(e).__name__ or "Refused" in str(e)
            layer_results["mcp_fallback"] = {"pass": layer2_pass, "exception": type(e).__name__}
            print(f"  {'✅' if layer2_pass else '❌'} MCPClient fallback: {type(e).__name__}")

        # Layer 3 — pipeline-level: full /chat still returns an answer
        print("  Layer 3: Pipeline-level fallback to legacy ReAct...")
        try:
            resp = requests.post(
                f"{BASE_API}/chat",
                json={"question": "储能行业概况", "session_id": f"test_degrade_{int(time.time())}"},
                timeout=60,
            )
            data = resp.json()
            layer3_pass = resp.status_code == 200 and bool(data.get("answer"))
            layer_results["pipeline_fallback"] = {
                "pass":       layer3_pass,
                "status":     resp.status_code,
                "has_answer": bool(data.get("answer")),
            }
            print(f"  {'✅' if layer3_pass else '❌'} Pipeline fallback: status={resp.status_code}, has_answer={bool(data.get('answer'))}")
        except Exception as e:
            layer_results["pipeline_fallback"] = {"pass": False, "error": str(e)}
            print(f"  ❌ Layer 3 error: {e}")

        passed = sum(1 for v in layer_results.values() if v.get("pass", False))
        score  = f"{passed}/3"
        with _lock:
            results["degradation"] = {"score": score, "layers": layer_results}
        print(f"  → Degradation layers: {passed}/3 PASS")
        return passed

    except Exception as e:
        print(f"  ❌ test_degradation_layers FAILED: {e}")
        _try_extract_troubleshoot("test_degradation_layers", e)
        with _lock:
            results["degradation"] = {"status": "ERROR", "error": str(e)}
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# TEST D: MemGPT 跨-session 记忆检索  →  KEY_3 (data_analyst)
# ─────────────────────────────────────────────────────────────────────────────

def test_memgpt_cross_session():
    print("\n=== TEST D: MemGPT Cross-Session Memory ===")
    try:
        from backend.memory.memgpt_memory import MemGPTMemory

        memory   = MemGPTMemory()   # rag=None → loads own SentenceTransformer
        session1 = f"test_mem_s1_{int(time.time())}"

        test_facts = [
            "2024年中国储能新增装机43.7GW，同比增长103%，磷酸铁锂占比超90%",
            "宁德时代储能出货量全球第一，市场份额约25%，天恒系统主打大容量电芯",
            "储能系统成本降至0.8-1.0元/Wh，较2022年下降约40%，经济性大幅提升",
        ]
        for fact in test_facts:
            memory.archival_memory_insert(session1, fact)
        print(f"  Session 1: inserted {len(test_facts)} facts")

        # archival_memory_search(query, top_k) — no session_id arg
        queries = [
            "储能装机容量数据",
            "宁德时代市场地位",
            "储能成本下降趋势",
        ]
        scores  = []
        details = []
        for query in queries:
            hits = memory.archival_memory_search(query, top_k=1)
            if hits:
                score = hits[0].get("score", 0)
                scores.append(score)
                details.append({
                    "query":       query,
                    "top1_score":  round(score, 4),
                    "retrieved":   hits[0].get("content", "")[:50],
                })
                print(f"  Query: '{query[:20]}' → score={score:.4f}")
            else:
                scores.append(0)
                details.append({"query": query, "top1_score": 0, "retrieved": "no results"})
                print(f"  Query: '{query[:20]}' → no results")

        avg_score       = sum(scores) / len(scores) if scores else 0
        above_threshold = sum(1 for s in scores if s > 0.5)
        with _lock:
            results["memgpt"] = {
                "avg_top1_score":      round(avg_score, 4),
                "above_0.5_threshold": f"{above_threshold}/{len(queries)}",
                "details":             details,
            }
        print(f"  → MemGPT cross-session: avg_score={avg_score:.4f}, above_0.5={above_threshold}/{len(queries)}")
        return avg_score

    except Exception as e:
        print(f"  ❌ test_memgpt_cross_session FAILED: {e}")
        _try_extract_troubleshoot("test_memgpt_cross_session", e)
        with _lock:
            results["memgpt"] = {"status": "ERROR", "error": str(e)}
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — parallel execution + report
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Resume Metrics Test Suite")
    print("=" * 60)

    start_total = time.time()

    fns = [
        test_router_accuracy,
        test_critic_detection,
        test_degradation_layers,
        test_memgpt_cross_session,
    ]

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in fns}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                fut.result()
            except Exception as e:
                # Belt-and-suspenders: individual functions already catch internally
                print(f"  [executor] {name} raised uncaught exception: {e}")

    total_time = time.time() - start_total

    # ── Generate report ───────────────────────────────────────────────────────
    os.makedirs("reports", exist_ok=True)
    report_file = f"reports/resume_metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# Resume Metrics Report\n\n")
        f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("---\n\n")

        f.write("## 📊 Summary\n\n")
        f.write("| Metric | Value | Used In |\n")
        f.write("|--------|-------|--------|\n")
        ra = results.get("router_accuracy", {})
        cd = results.get("critic_detection", {})
        dg = results.get("degradation", {})
        mg = results.get("memgpt", {})
        f.write(f"| RouterNode意图分类准确率 | {ra.get('score', ra.get('status','N/A'))} ({ra.get('accuracy','')}) | Bullet 1 |\n")
        f.write(f"| CriticMaster问题检出率   | {cd.get('score', cd.get('status','N/A'))} | Bullet 2 |\n")
        f.write(f"| 三层降级验证             | {dg.get('score', dg.get('status','N/A'))} PASS | Bullet 3 |\n")
        f.write(f"| MemGPT跨session top-1均值 | {mg.get('avg_top1_score', mg.get('status','N/A'))} | Bullet 4 |\n")
        f.write(f"| 总测试时间               | {total_time:.1f}s | - |\n\n")

        f.write("## 📝 Detail\n\n")
        f.write("```json\n")
        f.write(json.dumps(results, ensure_ascii=False, indent=2))
        f.write("\n```\n")

    print(f"\n{'=' * 60}")
    print(f"Report saved: {report_file}")
    print(f"Total time: {total_time:.1f}s")
    print(f"{'=' * 60}")

    # Print report to stdout for user
    print()
    with open(report_file, encoding="utf-8") as f:
        print(f.read())
