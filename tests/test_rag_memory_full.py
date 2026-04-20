"""
RAG + MemGPT 全链路测试
=========================
SECTION 1 — RAG质量 + ReAct多轮行动 (10题)
SECTION 2 — MemGPT记忆写入 + 更新测试 (6个)
SECTION 3 — RAG + MemGPT融合测试 (5个)

运行方式（项目根目录）:
    python tests/test_rag_memory_full.py
"""

import json
import logging
import os
import re
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

# ── Shared singletons (reuse already-loaded BGE-m3 / Milvus / Redis) ─────────
print("加载 LangGraph agent 模块（BGE-m3 + Milvus + Redis）…")
import langgraph_agent as _lga

_rag   = _lga._rag
memgpt = _lga.memgpt
graph  = _lga.build_graph()
print("就绪。\n")

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def ensure_ingested() -> None:
    """确认两份PDF已ingested到知识库，未ingested则自动执行。"""
    needed = ["vectorDB_test_document.pdf", "HR_test_document.pdf"]
    sources = set(_rag.list_sources())
    print("【前置准备】检查知识库文档：")
    for fname in needed:
        if fname in sources:
            print(f"  [OK] {fname}：已在知识库")
        else:
            path = os.path.join("resources", "test_files", fname)
            if os.path.exists(path):
                print(f"  → 正在 ingest {fname} …")
                n = _rag.ingest_file(path)
                print(f"     完成，插入 {n} 个 chunk")
            else:
                print(f"  [WARN] 警告：{fname} 不存在于 {path}，相关测试可能失败")
    print()


def run_session(question: str, session_id: str) -> tuple[dict, list[str], float]:
    """
    运行一个完整的 LangGraph session，返回 (final_state, reflector_decisions, elapsed_s)。
    使用 stream() 以便捕捉每轮 Reflector 的 decision。
    """
    init = {
        "question":       question,
        "intent":         "",
        "plan":           [],
        "steps_executed": [],
        "reflection":     "",
        "confidence":     0.0,
        "final_answer":   "",
        "iteration":      0,
        "session_id":     session_id,
    }
    t0 = time.time()
    reflector_decisions: list[str] = []
    state = dict(init)

    for event in graph.stream(init):
        for node_name, update in event.items():
            if node_name == "reflector" and isinstance(update, dict):
                try:
                    d = json.loads(update.get("reflection", "{}")).get("decision", "?")
                    reflector_decisions.append(d)
                except Exception:
                    pass
            if isinstance(update, dict):
                state.update(update)

    return state, reflector_decisions, time.time() - t0


def check_result(answer: str, expected_kw: list[str] = None,
                 forbidden_kw: list[str] = None) -> str:
    """
    返回 'PASS' / 'PARTIAL' / 'FAIL'。
    forbidden_kw 命中 → 强制 FAIL。
    expected_kw 全部命中 → PASS；部分 → PARTIAL；全未命中 → FAIL。
    """
    if forbidden_kw:
        for kw in forbidden_kw:
            if kw in answer:
                return "FAIL"
    if expected_kw:
        hits = [kw for kw in expected_kw if kw in answer]
        if len(hits) == len(expected_kw):
            return "PASS"
        if hits:
            return "PARTIAL"
        return "FAIL"
    return "PASS"


def tools_used(state: dict) -> list[str]:
    """从 steps_executed 提取工具调用列表（排除 memory 内部步骤）。"""
    return [
        s.get("action", "?")
        for s in state.get("steps_executed", [])
    ]


def rag_metrics(question: str) -> tuple[int, float]:
    """直接调用 _rag.query() 获取 chunk 数和最高相似度。"""
    hits = _rag.query(question, top_k=5)
    if not hits:
        return 0, 0.0
    return len(hits), round(max(h["score"] for h in hits), 3)


class CapturePrompts:
    """上下文管理器：monkey-patch _lga._llm.chat_json 以捕获所有 system prompt。"""
    def __enter__(self):
        self.calls: list[str] = []
        _orig = _lga._llm.chat_json
        self._orig = _orig
        captured = self.calls
        def _patched(system, user, temperature=0.2):
            captured.append(system)
            return _orig(system, user, temperature=temperature)
        _lga._llm.chat_json = _patched
        return self

    def __exit__(self, *_):
        _lga._llm.chat_json = self._orig

    def planner_prompt(self) -> str:
        """返回含 [记忆] 前缀的 planner system prompt（若有）。"""
        for p in self.calls:
            if "[记忆]" in p[:60]:
                return p
        # fallback: second call (router=0, planner=1)
        return self.calls[1] if len(self.calls) > 1 else ""


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — RAG质量 + ReAct多轮行动
# ═══════════════════════════════════════════════════════════════════════════════

SECTION1_TESTS = [
    {
        "id": "VDB-1", "tags": ["factual"], "session": "s1_vdb1",
        "question": "VectorDB Pro当前最新稳定版本是什么？该版本是何时发布的？",
        "expected": ["3.2", "2024年9月15日"], "forbidden": [],
        "min_steps": 1,
    },
    {
        "id": "VDB-2", "tags": ["negation"], "session": "s1_vdb2",
        "question": "VectorDB Pro v3.2是否仍然支持ANNOY索引？",
        "expected": ["不支持", "废弃"], "forbidden": ["支持ANNOY"],
        "min_steps": 1,
    },
    {
        "id": "VDB-3", "tags": ["numerical", "table", "multi-step"], "session": "s1_vdb3",
        "question": (
            "我需要为生产系统选择索引。请告诉我v3.2 HNSW和IVF-PQ的P99延迟分别是多少？"
            "QPS分别是多少？内存占用分别是多少？给出选择建议。"
        ),
        "expected": ["4.2", "6.8", "142,000", "98,000", "82", "31"], "forbidden": [],
        "min_steps": 2,
    },
    {
        "id": "VDB-4", "tags": ["unanswerable"], "session": "s1_vdb4",
        "question": "VectorDB Pro v3.2的月度订阅价格是多少？",
        "expected": ["未提及", "无法", "文档中没有"], "forbidden": [],
        "min_steps": 1,
        "price_guard": True,
    },
    {
        "id": "VDB-5", "tags": ["conflict", "multi-step"], "session": "s1_vdb5",
        "question": (
            "请先告诉我VectorDB Pro v3.1的Collection数量限制，"
            "再告诉我v3.2各版本的限制，并说明两处描述是否存在矛盾。"
        ),
        "expected": ["65,536", "10,000"], "forbidden": [],
        "min_steps": 2,
    },
    {
        "id": "HR-1", "tags": ["negation"], "session": "s1_hr1",
        "question": "工龄奖励假目前是否仍然有效？工龄满5年的员工能否申请？",
        "expected": ["取消", "废止", "不再"], "forbidden": ["可以申请", "满5年享有"],
        "min_steps": 1,
    },
    {
        "id": "HR-2", "tags": ["table", "conditional"], "session": "s1_hr2",
        "question": "一名连续工龄恰好满8年的员工，每年享有多少天年假？",
        "expected": ["20"], "forbidden": ["15天"],
        "min_steps": 1,
    },
    {
        "id": "HR-3", "tags": ["numerical", "multi-step"], "session": "s1_hr3",
        "question": (
            "帮我计算年终奖：员工4月15日入职，绩效A，月薪20000元。"
            "请先告诉我A级系数，再说明不足12个月的规则，最后给出金额。"
        ),
        "expected": ["45,000", "3", "9/12"], "forbidden": [],
        "min_steps": 2,
    },
    {
        "id": "HR-4", "tags": ["conditional", "negation"], "session": "s1_hr4",
        "question": "员工提交离职申请后，未使用年假补偿比例是150%还是100%？",
        "expected": ["100%"], "forbidden": ["150%"],
        "min_steps": 1,
    },
    {
        "id": "HR-5", "tags": ["multi-step"], "session": "s1_hr5",
        "question": (
            "试用期员工连续请了12个工作日病假。"
            "请分三步回答：带薪上限多少天？超出如何处理？公司有权解除合同吗？"
        ),
        "expected": ["3天", "无薪", "10个工作日"], "forbidden": [],
        "min_steps": 2,
    },
]


def run_section1() -> list[dict]:
    print("=" * 60)
    print("SECTION 1 — RAG质量 + ReAct多轮行动")
    print("=" * 60)

    results = []
    for spec in SECTION1_TESTS:
        sid = spec["session"]
        memgpt._redis.delete(f"core_memory:{sid}")   # 隔离：清除残余记忆

        state, r_decisions, elapsed = run_session(spec["question"], sid)
        answer  = state.get("final_answer", "")
        steps   = state.get("steps_executed", [])
        t_used  = tools_used(state)
        n_steps = len([s for s in steps if s.get("action") != "archival_memory_search"])

        # Keyword check
        verdict = check_result(answer, spec.get("expected"), spec.get("forbidden"))

        # VDB-4 price guard
        if spec.get("price_guard") and verdict != "FAIL":
            if re.search(r"\$[\d,]+|\d+\s*元[/每]月|\d+\s*元[/每]年|\d+\s*USD", answer):
                verdict = "FAIL"

        # multi-step step count check
        step_warn = ""
        if n_steps < spec.get("min_steps", 1):
            step_warn = " WARN:steps不足"

        # RAG metrics (direct query)
        n_chunks, top_score = rag_metrics(spec["question"])

        tags_str = ",".join(spec["tags"])
        label    = f"{verdict}{step_warn}"
        print(f"\n{spec['id']} [{tags_str}] — {label} (steps={n_steps}, {elapsed:.1f}s)")
        print(f"  Answer前200字: {answer[:200]}")
        print(f"  RAG chunks: {n_chunks}  最高相似度: {top_score}")
        print(f"  工具调用: {t_used}")
        print(f"  Reflector decisions: {r_decisions}")
        print(f"  耗时: {elapsed:.1f}s")

        results.append({
            "id": spec["id"], "tags": spec["tags"],
            "verdict": verdict, "warn": bool(step_warn),
            "steps": n_steps, "elapsed": elapsed,
        })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — MemGPT记忆写入 + 更新测试
# ═══════════════════════════════════════════════════════════════════════════════

def run_section2() -> list[dict]:
    print("\n" + "=" * 60)
    print("SECTION 2 — MemGPT记忆写入 + 更新测试")
    print("=" * 60)

    results = []

    # ── MEM-1: Core Memory首次写入 ───────────────────────────────────────────
    print("\n--- MEM-1 [Core Memory首次写入] ---")
    memgpt._redis.delete("core_memory:mem_user_01")
    q = "我是一名数据工程师，主要负责华北地区的业务，对IVF-PQ索引特别感兴趣，以后的分析都聚焦这个方向"
    state, _, elapsed = run_session(q, "mem_user_01")
    human = memgpt.get_core_memory("mem_user_01")["human"]
    kws   = ["数据工程师", "华北", "IVF-PQ"]
    hits  = [kw for kw in kws if kw in human]
    verdict = "PASS" if len(hits) == len(kws) else ("PARTIAL" if hits else "FAIL")
    print(f"MEM-1 [Core Memory首次写入] — {verdict}")
    print(f"  写入后 human block: {human[:200]} ({len(human)} chars)")
    print(f"  包含关键词: {hits}")
    results.append({"id": "MEM-1", "verdict": verdict})

    # ── MEM-2: Archival写入验证 ──────────────────────────────────────────────
    print("\n--- MEM-2 [Archival写入验证] ---")
    memgpt._redis.delete("core_memory:mem_arch_02")
    before = memgpt._archival.num_entities
    q = "帮我查询华北地区上个季度所有产品类别的销售总额，并总结哪个类别表现最好"
    state, _, elapsed = run_session(q, "mem_arch_02")
    after   = memgpt._archival.num_entities
    inserted = after > before
    verdict  = "PASS" if inserted else "FAIL"
    # 展示最新插入的内容
    archived_preview = ""
    if inserted:
        recent = memgpt.archival_memory_search("华北销售", top_k=1)
        if recent:
            archived_preview = recent[0]["content"][:150]
    print(f"MEM-2 [Archival写入验证] — {verdict}")
    print(f"  archival entities: {before} → {after} (inserted={inserted})")
    print(f"  archival_memory_insert: {'triggered' if inserted else 'NOT triggered'}")
    print(f"  归档内容预览: {archived_preview}")
    results.append({"id": "MEM-2", "verdict": verdict})

    # ── MEM-3: 跨session Archival检索 (依赖MEM-2) ───────────────────────────
    print("\n--- MEM-3 [跨session Archival检索] ---")
    memgpt._redis.delete("core_memory:mem_arch_03")
    q = "上次分析华北地区销售的结论是什么？"
    state, _, elapsed = run_session(q, "mem_arch_03")
    steps = state.get("steps_executed", [])
    search_step = next(
        (s for s in steps if s.get("action") == "archival_memory_search"), None
    )
    search_triggered = search_step is not None
    top1_score = 0.0
    top1_content = ""
    if search_triggered and isinstance(search_step.get("result"), list) and search_step["result"]:
        top1_score   = search_step["result"][0].get("score", 0.0)
        top1_content = search_step["result"][0].get("content", "")
    verdict = "PASS" if search_triggered and top1_score >= 0.5 else (
              "PARTIAL" if search_triggered else "FAIL")
    print(f"MEM-3 [跨session Archival检索] — {verdict}")
    print(f"  archival_memory_search: {'triggered' if search_triggered else 'NOT triggered'}")
    print(f"  top1 score: {top1_score:.4f}")
    print(f"  检索到的内容: {top1_content[:150]}")
    results.append({"id": "MEM-3", "verdict": verdict})

    # ── MEM-4: Core Memory更新 (依赖MEM-1) ──────────────────────────────────
    print("\n--- MEM-4 [Core Memory更新] ---")
    before_human = memgpt.get_core_memory("mem_user_01")["human"]
    q = "我换岗位了，现在负责华南区域，不再关注IVF-PQ了，改为研究HNSW调优"
    state, _, elapsed = run_session(q, "mem_user_01")
    after_human = memgpt.get_core_memory("mem_user_01")["human"]
    kws_new = ["华南", "HNSW"]
    hits_new = [kw for kw in kws_new if kw in after_human]
    verdict = "PASS" if len(hits_new) == 2 else ("PARTIAL" if hits_new else "FAIL")
    # Detect memory action
    mem_step = next(
        (s for s in state.get("steps_executed", [])
         if s.get("action") in ("archival_memory_search", "core_memory_replace",
                                "core_memory_append")), None
    )
    action_used = mem_step["action"] if mem_step else "core_memory_append(via reflector)"
    print(f"MEM-4 [Core Memory更新] — {verdict}")
    print(f"  更新前 human block: {before_human[:100]} ({len(before_human)} chars)")
    print(f"  更新后 human block: {after_human[:100]} ({len(after_human)} chars)")
    print(f"  包含新关键词: {hits_new}")
    print(f"  memory_action: {action_used}")
    results.append({"id": "MEM-4", "verdict": verdict})

    # ── MEM-5: Core Memory FIFO上限 ──────────────────────────────────────────
    print("\n--- MEM-5 [Core Memory FIFO上限] ---")
    memgpt._redis.delete("core_memory:mem_cap_05")
    content_unit = "这是一段测试内容，用于验证FIFO截断机制。包含华北销售数据分析相关的重要信息。" * 4  # ~120 chars
    # pad to ~250 chars
    content_unit = (content_unit + "补充信息" * 30)[:250]
    first_content = f"首次内容_{uuid.uuid4().hex[:6]}_" + content_unit[:50]
    last_content  = f"最终内容_{uuid.uuid4().hex[:6]}_" + content_unit[:50]

    all_contents = [first_content] + [content_unit] * 6 + [last_content]
    print(f"  追加 {len(all_contents)} 次，每次 ~250 字符")
    print(f"  {'追加#':<8} {'human长度':>12} {'总计(persona+human)':>22}")
    print(f"  {'─'*44}")
    for i, c in enumerate(all_contents, 1):
        memgpt.core_memory_append("mem_cap_05", "human", c)
        mem   = memgpt.get_core_memory("mem_cap_05")
        total = len(mem["persona"]) + len(mem["human"])
        print(f"  {i:<8} {len(mem['human']):>12} {total:>22}")

    final_mem   = memgpt.get_core_memory("mem_cap_05")
    final_total = len(final_mem["persona"]) + len(final_mem["human"])
    cap_ok      = final_total <= 2000
    latest_ok   = last_content[:20] in final_mem["human"]
    oldest_gone = first_content[:20] not in final_mem["human"]
    verdict = "PASS" if (cap_ok and latest_ok and oldest_gone) else "FAIL"
    print(f"\n  最终总计: {final_total} 字符（上限: 2000）{'[OK]' if cap_ok else '[FAIL]'}")
    print(f"  最新内容存在: {latest_ok}")
    print(f"  最旧内容已截断: {oldest_gone}")
    print(f"MEM-5 [Core Memory FIFO上限] — {verdict}")
    results.append({"id": "MEM-5", "verdict": verdict})

    # ── MEM-6: 记忆注入影响Planner决策 ──────────────────────────────────────
    print("\n--- MEM-6 [记忆注入影响Planner决策] ---")
    memgpt._redis.delete("core_memory:mem_inject_06")
    inject_content = "用户是销售分析师，偏好用text2sql查询结构化数据"
    memgpt.core_memory_replace("mem_inject_06", "human", inject_content)

    with CapturePrompts() as cap:
        state, _, elapsed = run_session("帮我分析一下最近的销售情况", "mem_inject_06")

    planner_sys = cap.planner_prompt()
    prompt_has_inject = inject_content in planner_sys[:500]
    t_used = tools_used(state)
    sql_used = "text2sql" in t_used
    verdict = "PASS" if (prompt_has_inject and sql_used) else (
              "PARTIAL" if (prompt_has_inject or sql_used) else "FAIL")
    print(f"MEM-6 [记忆注入影响Planner决策] — {verdict}")
    print(f"  Planner prompt前300字包含注入内容: {prompt_has_inject}")
    print(f"  Planner prompt前300字: {planner_sys[:300]}")
    print(f"  tools_used: {t_used}")
    print(f"  text2sql 出现: {sql_used}")
    results.append({"id": "MEM-6", "verdict": verdict})

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — RAG + MemGPT融合测试
# ═══════════════════════════════════════════════════════════════════════════════

def run_section3() -> list[dict]:
    print("\n" + "=" * 60)
    print("SECTION 3 — RAG + MemGPT融合测试")
    print("=" * 60)

    results = []

    # ── FUS-1: RAG结论自动归档 ────────────────────────────────────────────────
    print("\n--- FUS-1 [RAG结论自动归档] ---")
    memgpt._redis.delete("core_memory:fus_01")
    before = memgpt._archival.num_entities
    q = "详细解释VectorDB Pro中HNSW和IVF-PQ各自的适用场景以及核心性能差异"
    state, r_dec, elapsed = run_session(q, "fus_01")
    after   = memgpt._archival.num_entities
    t_used  = tools_used(state)
    rag_ok  = "rag_search" in t_used
    arch_ok = after > before
    verdict = "PASS" if (rag_ok and arch_ok) else ("PARTIAL" if (rag_ok or arch_ok) else "FAIL")
    archived = memgpt.archival_memory_search("HNSW IVF-PQ 适用场景", top_k=1)
    arch_preview = archived[0]["content"][:150] if archived else "(无)"
    print(f"FUS-1 [RAG结论自动归档] — {verdict}")
    print(f"  rag_search triggered: {rag_ok}")
    print(f"  archival_memory_insert triggered: {arch_ok} ({before}→{after})")
    print(f"  归档内容: {arch_preview}")
    print(f"  tools_used: {t_used}")
    results.append({"id": "FUS-1", "verdict": verdict})

    # ── FUS-2: Archival记忆增强RAG (依赖FUS-1) ──────────────────────────────
    print("\n--- FUS-2 [Archival记忆增强RAG] ---")
    memgpt._redis.delete("core_memory:fus_02")
    q = "基于我们之前讨论的索引知识，如果我的系统有128GB内存，应该选哪种索引？"
    state, r_dec, elapsed = run_session(q, "fus_02")
    steps  = state.get("steps_executed", [])
    t_used = tools_used(state)
    arch_search = any(s.get("action") == "archival_memory_search" for s in steps)
    rag_used    = "rag_search" in t_used
    n_steps     = len([s for s in steps if s.get("action") != "archival_memory_search"])
    answer      = state.get("final_answer", "")
    hist_ref    = any(kw in answer for kw in ["基于", "之前", "历史", "上次", "我们讨论"])
    verdict = "PASS" if (arch_search and rag_used and n_steps >= 2) else (
              "PARTIAL" if (arch_search or rag_used) else "FAIL")
    print(f"FUS-2 [Archival记忆增强RAG] — {verdict}")
    print(f"  archival_memory_search: {arch_search}")
    print(f"  rag_search: {rag_used}")
    print(f"  steps (非memory): {n_steps}")
    print(f"  answer引用历史: {hist_ref}")
    print(f"  Answer前200字: {answer[:200]}")
    results.append({"id": "FUS-2", "verdict": verdict})

    # ── FUS-3: 跨文档多轮推理 (VDB + HR) ────────────────────────────────────
    print("\n--- FUS-3 [跨文档多轮推理] ---")
    memgpt._redis.delete("core_memory:fus_03")
    q = "VectorDB Pro专业版的数据保留期限是多久？另外，公司员工的带薪病假是多少天？"
    state, r_dec, elapsed = run_session(q, "fus_03")
    answer = state.get("final_answer", "")
    verdict = check_result(answer, ["180天", "12天"])
    # Check if RAG retrieved from both sources
    rag_hits = _rag.query(q, top_k=5)
    sources_hit = {h["source"] for h in rag_hits}
    two_docs = (
        any("vectorDB" in s.lower() or "vector" in s.lower() for s in sources_hit) and
        any("hr" in s.lower() or "HR" in s.lower() for s in sources_hit)
    )
    print(f"FUS-3 [跨文档多轮推理] — {verdict}")
    print(f"  answer包含['180天','12天']: {check_result(answer, ['180天','12天'])}")
    print(f"  RAG命中sources: {sources_hit}")
    print(f"  命中两个不同文档: {two_docs}")
    print(f"  Answer前200字: {answer[:200]}")
    results.append({"id": "FUS-3", "verdict": verdict})

    # ── FUS-4: 三路协同 (Core Memory + RAG + Text2SQL) ───────────────────────
    print("\n--- FUS-4 [三路协同] ---")
    memgpt._redis.delete("core_memory:fus_04")
    inject = "用户是华南区销售总监，关注电子产品业务，同时在研究向量数据库选型"
    memgpt.core_memory_replace("fus_04", "human", inject)

    with CapturePrompts() as cap:
        state, r_dec, elapsed = run_session(
            "给我一个综合报告：华南区电子产品的销售数据怎么样，以及HNSW索引的内存要求是多少",
            "fus_04",
        )

    t_used      = tools_used(state)
    planner_sys = cap.planner_prompt()
    sql_ok  = "text2sql" in t_used
    rag_ok  = "rag_search" in t_used
    plan    = state.get("plan", [])
    plan_ok = len(plan) >= 2
    mem_ok  = inject[:15] in planner_sys[:500]
    verdict = "PASS" if (sql_ok and rag_ok and plan_ok) else (
              "PARTIAL" if (sql_ok or rag_ok) else "FAIL")
    print(f"FUS-4 [三路协同] — {verdict}")
    print(f"  text2sql in tools: {sql_ok}")
    print(f"  rag_search in tools: {rag_ok}")
    print(f"  Planner steps: {len(plan)}")
    print(f"  Core Memory注入到Planner: {mem_ok}")
    print(f"  tools_used: {t_used}")
    results.append({"id": "FUS-4", "verdict": verdict})

    # ── FUS-5: 记忆更新影响路由 AB对照 ──────────────────────────────────────
    print("\n--- FUS-5 [路由变化对照] ---")
    q_same = "帮我分析一下数据库相关的内容"

    # 5a: 无注入
    memgpt._redis.delete("core_memory:fus_05a")
    state_a, _, _ = run_session(q_same, "fus_05a")
    tools_a = tools_used(state_a)

    # 5b: 注入 SQL 偏好
    memgpt._redis.delete("core_memory:fus_05b")
    memgpt.core_memory_replace(
        "fus_05b", "human",
        "用户只关心结构化销售数据，所有数据库问题都用SQL查询"
    )
    state_b, _, _ = run_session(q_same, "fus_05b")
    tools_b = tools_used(state_b)

    tools_differ = set(tools_a) != set(tools_b)
    verdict = "PASS" if tools_differ else "PARTIAL"
    print(f"FUS-5 [路由变化对照] — {verdict}")
    print(f"  5a (无注入) tools_used: {tools_a}")
    print(f"  5b (SQL偏好注入) tools_used: {tools_b}")
    print(f"  工具选择存在差异: {tools_differ}（人工确认是否合理）")
    results.append({"id": "FUS-5", "verdict": verdict})

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Final Summary Report
# ═══════════════════════════════════════════════════════════════════════════════

def print_summary(s1: list[dict], s2: list[dict], s3: list[dict]) -> None:
    line = "=" * 60

    print("\n" + line)
    print("RAG + MemGPT 全链路测试报告")
    print(line)

    # SECTION 1
    print("SECTION 1 — RAG质量 + ReAct多轮行动")
    tag_pass: dict[str, list] = {}
    for r in s1:
        v = r["verdict"]
        w = "  WARN" if r.get("warn") else ""
        s_info = f"  steps={r['steps']}" if r.get("steps") else ""
        verdict_str = f"{v}{w}"
        t_str = f"{r['elapsed']:.1f}s"
        pad   = 38 - len(r["id"]) - len(",".join(r["tags"]))
        print(f"  {r['id']:6s} [{','.join(r['tags'])}]{' '*max(0,pad-len(','.join(r['tags'])))} {verdict_str:<12} {t_str}{s_info}")
        for tag in r["tags"]:
            tag_pass.setdefault(tag, []).append(v == "PASS")

    s1_pass    = sum(1 for r in s1 if r["verdict"] == "PASS")
    s1_partial = sum(1 for r in s1 if r["verdict"] == "PARTIAL")
    s1_fail    = sum(1 for r in s1 if r["verdict"] == "FAIL")
    ms_tests   = [r for r in s1 if "multi-step" in r["tags"]]
    ms_avg     = (sum(r["steps"] for r in ms_tests) / len(ms_tests)) if ms_tests else 0

    print(f"  {'─'*54}")
    print(f"  RAG得分: {s1_pass}/10 (PASS) {s1_partial}/10 (PARTIAL) {s1_fail}/10 (FAIL)")
    print(f"  multi-step题目平均steps: {ms_avg:.1f}")
    print("  按维度统计:")
    for tag in ["negation", "multi-step", "table", "unanswerable", "conflict"]:
        vals = tag_pass.get(tag, [])
        print(f"    {tag:<15}: {sum(vals)}/{len(vals)}")

    # SECTION 2
    print("\nSECTION 2 — MemGPT记忆测试")
    labels2 = {
        "MEM-1": "Core Memory首次写入",
        "MEM-2": "Archival写入验证",
        "MEM-3": "跨session检索",
        "MEM-4": "Core Memory更新",
        "MEM-5": "FIFO上限",
        "MEM-6": "记忆注入影响Planner",
    }
    for r in s2:
        label = labels2.get(r["id"], r["id"])
        print(f"  {r['id']} {label:<26} {r['verdict']}")
    s2_pass = sum(1 for r in s2 if r["verdict"] == "PASS")
    print(f"  {'─'*54}")
    print(f"  记忆测试: {s2_pass}/6 PASS")

    # SECTION 3
    print("\nSECTION 3 — 融合测试")
    labels3 = {
        "FUS-1": "RAG结论自动归档",
        "FUS-2": "Archival增强RAG",
        "FUS-3": "跨文档多轮推理",
        "FUS-4": "三路协同",
        "FUS-5": "路由变化对照",
    }
    for r in s3:
        label = labels3.get(r["id"], r["id"])
        print(f"  {r['id']} {label:<26} {r['verdict']}")
    s3_pass = sum(1 for r in s3 if r["verdict"] == "PASS")
    print(f"  {'─'*54}")
    print(f"  融合测试: {s3_pass}/5 PASS")

    # Total
    total_pass = s1_pass + s2_pass + s3_pass
    all_elapsed = [r.get("elapsed", 0) for r in s1]
    avg_elapsed = sum(all_elapsed) / len(all_elapsed) if all_elapsed else 0
    print(f"\n总体: {total_pass}/21")
    print(f"平均响应时间（SECTION 1）: {avg_elapsed:.1f}s")
    print(line)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ensure_ingested()
    s1 = run_section1()
    s2 = run_section2()
    s3 = run_section3()
    print_summary(s1, s2, s3)
