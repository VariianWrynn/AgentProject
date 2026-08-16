# 面试材料事实性修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除简历与报告中三处经不起面试官交叉验证的事实性错误（perf 中位数拼接、Milvus 索引误配、Bullet 1 因果链虚构），并把三处夸大措辞改为如实表述。

**Architecture:** 三条独立的修复线，每条都是「先让错误可被测试捕获 → 修代码 → 用真实数据重新出数 → 回填文档」。不新增依赖，不改动 agent 业务逻辑。

**Tech Stack:** Python 3（conda env `agentPro`）、pymilvus、pytest。运行前提：Docker 中 Milvus(19530)/Redis(6379) 在线；Task 4/5 需 MCP server 在 :8002。

**统一运行前缀**（系统 python 无 torch，必须用 conda env）：
```
PY="C:/Users/77837/miniconda3/envs/agentPro/python.exe"
export HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8
```

---

## 问题清单（本计划要消灭的）

| # | 问题 | 性质 | 任务 |
|---|------|------|------|
| 1 | `report()` 每列独立取中位数 → 表格同一行来自不同 run，1.66× 是跨 query 相除 | 代码 bug + 数据错误 | Task 1–2 |
| 2 | `IVF_FLAT nlist=1024` 用在 161 个向量上，nprobe=64 只搜 6.25% 簇 | 设计错误 | Task 3–4 |
| 3 | Layer-3 兜底仅 mock 验证，真实回退路径从未跑过 | 验证不足 | Task 5 |
| 4 | Bullet 1 把 wk4 的 28/30（5 节点 pipeline、2 例 non-deterministic）说成「单 Agent ReAct 的问题，失败例定位为 Router 误判」 | 虚构因果 | Task 6 |
| 5 | 「上市公司财报」实为财经媒体报道稿（最短 209 字） | 措辞夸大 | Task 6 |
| 6 | 「基线 0.5」是自设阈值；27.5%→15.0% 在 n=40 上 p=0.274 不显著 | 缺少限定 | Task 6 |

## File Structure

| 文件 | 职责 |
|------|------|
| `scripts/perf_benchmark.py` | 修 `report()`：行内配对 + 逐题比值 |
| `tests/test_perf_report.py` | **新建** — 锁死"同一行同一 run"与"逐题配对"两条不变量 |
| `rag_pipeline.py` | 索引类型可配置 + `rebuild_index()` + nlist 合法性校验 |
| `tests/test_index_config.py` | **新建** — 纯函数校验索引/检索参数，无需 Milvus |
| `scripts/rebuild_index.py` | **新建** — 就地重建索引（保留实体） |
| `docs/reports/perf_20260813.md`、`fact_eval_runs/retrieval_result.json` | 重新出数 |
| `docs/interview-prep/resume_deepresearch_v3.md`、`resume_bullets_v2.md` | 回填诚实表述 |
| `docs/checkpoints/interview-opt-checkpoint.md` | 记录修正 |

---

## Task 1: 修复 perf 报告的中位数拼接 bug

**Files:**
- Create: `tests/test_perf_report.py`
- Modify: `scripts/perf_benchmark.py`（`report()` 内的 `med()` 及其调用处）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_perf_report.py`：

```python
"""
tests/test_perf_report.py — 锁死性能报告的两条统计不变量。

背景：初版 report() 对每一列独立取中位数，导致表格同一行的数字来自不同
的 run（出现「token 更多但成本更低」的自相矛盾），且 1.66× 提速是拿
q2 的耗时除以 q0 的耗时得出的。这两个测试防止该错误复发。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.perf_benchmark import _median, _median_run, _paired_ratios  # noqa: E402


def test_median_run_keeps_row_internally_consistent():
    """整行必须来自同一次运行，而非逐列取中位数拼接。"""
    runs = [
        {"elapsed_s": 300.0, "prompt_tokens": 10, "cost_usd": 0.03},
        {"elapsed_s": 100.0, "prompt_tokens": 30, "cost_usd": 0.01},
        {"elapsed_s": 200.0, "prompt_tokens": 20, "cost_usd": 0.02},
    ]
    r = _median_run(runs)
    assert (r["elapsed_s"], r["prompt_tokens"], r["cost_usd"]) == (200.0, 20, 0.02)


def test_paired_ratios_compares_within_same_query():
    """比值只能在同一 query 内部计算，跨 query 相除没有意义。"""
    recs = [
        {"config": "a", "query_idx": 0, "elapsed_s": 400.0},
        {"config": "b", "query_idx": 0, "elapsed_s": 200.0},
        {"config": "a", "query_idx": 1, "elapsed_s": 300.0},
        {"config": "b", "query_idx": 1, "elapsed_s": 300.0},
    ]
    assert _paired_ratios(recs, "a", "b", "elapsed_s") == [2.0, 1.0]


def test_paired_ratios_skips_incomplete_pairs():
    """某个 query 缺一侧数据时整对丢弃，不能用别的 query 顶替。"""
    recs = [
        {"config": "a", "query_idx": 0, "elapsed_s": 400.0},
        {"config": "b", "query_idx": 0, "elapsed_s": 200.0},
        {"config": "a", "query_idx": 1, "elapsed_s": 300.0},
    ]
    assert _paired_ratios(recs, "a", "b", "elapsed_s") == [2.0]


def test_median_of_empty_is_zero():
    assert _median([]) == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

```bash
$PY -m pytest tests/test_perf_report.py -v
```
预期：`ImportError: cannot import name '_median_run'`（4 个用例全部 collection error）

- [ ] **Step 3: 在 `scripts/perf_benchmark.py` 中把 `report()` 里的 `def med(vals)` 整段删除，并在 `def report()` 之前插入三个模块级函数**

```python
def _median(vals: list[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2] if s else 0.0


def _median_run(rs: list[dict]) -> dict:
    """返回 elapsed_s 处于中位的那一次运行。

    报告一行里的每个数字都必须来自同一次运行——逐列独立取中位数会混合
    不同 run，产出自相矛盾的行（例如 token 更多却成本更低）。
    """
    return sorted(rs, key=lambda r: r["elapsed_s"])[len(rs) // 2]


def _paired_ratios(recs: list[dict], base_cfg: str, cfg: str, key: str) -> list[float]:
    """逐 query 计算 base/cfg 比值——每个 (config, query) 只有 n=1 时，
    这是唯一有意义的比较方式。缺任一侧的 query 整对丢弃。"""
    by_q: dict[int, dict[str, dict]] = {}
    for r in recs:
        by_q.setdefault(r["query_idx"], {})[r["config"]] = r
    out: list[float] = []
    for q in sorted(by_q):
        cell = by_q[q]
        if base_cfg in cell and cfg in cell:
            out.append(cell[base_cfg][key] / cell[cfg][key])
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```bash
$PY -m pytest tests/test_perf_report.py -v
```
预期：`4 passed`

- [ ] **Step 5: 把 `report()` 的表格与结论段改为使用新函数**

将 `report()` 中从 `date = datetime.now()...` 到写文件之前的整段替换为：

```python
    date = datetime.now().strftime("%Y%m%d")
    n_q = len({r["query_idx"] for r in recs})
    lines = ["# 性能与成本基准（单份完整报告, demo_mode=False, FACT_GUARD=on）", "",
             f"**日期:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"**固定 query:** {n_q} 条（政策/财报/市场各1），每个配置每条 query 各跑 1 次",
             "",
             "> 统计口径：表格取**中位耗时那一次运行的实测值**（整行同源）；",
             "> 提速/成本结论取**逐 query 配对比值**的中位数与区间。",
             "> 每格样本 n=1，LLM 延迟波动大，比值区间比点估计更可信。",
             "",
             "| 配置 | 耗时 | prompt tok | completion tok | LLM 调用 | 成本(USD) |",
             "|---|---|---|---|---|---|"]
    for cfg in ("serial_nocache", "parallel_cache", "tiered"):
        rs = by_cfg.get(cfg, [])
        if not rs:
            continue
        r = _median_run(rs)
        lines.append(
            f"| {cfg} | {r['elapsed_s']:.0f}s | {r['prompt_tokens']} "
            f"| {r['completion_tokens']} | {r['llm_calls']} | ${r['cost_usd']:.4f} |")

    lines += ["", "## 逐 query 配对比较", "",
              "| 对比 | 各 query 比值 | 中位 |", "|---|---|---|"]
    for label, base, cfg, key, fmt in [
        ("并行+缓存 提速 (serial→parallel)", "serial_nocache", "parallel_cache", "elapsed_s", "x"),
        ("再叠加模型分级 提速 (serial→tiered)", "serial_nocache", "tiered", "elapsed_s", "x"),
        ("模型分级 省钱 (parallel→tiered)", "parallel_cache", "tiered", "cost_usd", "x"),
    ]:
        rr = _paired_ratios(recs, base, cfg, key)
        if not rr:
            continue
        cells = " / ".join(f"{v:.2f}{fmt}" for v in rr)
        lines.append(f"| {label} | {cells} | **{_median(rr):.2f}{fmt}** |")

    lat = _paired_ratios(recs, "parallel_cache", "tiered", "elapsed_s")
    if lat:
        lines += ["",
                  f"**结论：** 提速主要来自 asyncio 并行 + Redis 缓存；模型分级对延迟"
                  f"无稳定收益（parallel→tiered 比值 "
                  f"{' / '.join(f'{v:.2f}x' for v in lat)}，含劣化），其价值在成本。"]
    lines += ["", f"**单价假设(USD/1M tok):** {json.dumps(PRICES)}（引用前请与供应商牌价核对）", ""]
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_perf_report.py scripts/perf_benchmark.py
git commit -m "fix: perf report mixed per-column medians across runs

Rows now come from a single run and speedup/cost claims use per-query
paired ratios. The old code divided q2 latency by q0 latency to produce
a bogus 1.66x."
```

---

## Task 2: 用现有数据重新生成诚实的性能报告

**Files:**
- Create: `docs/reports/perf_20260813.md`（由脚本生成）
- 数据源：`docs/reports/fact_eval_runs/perf_runs.jsonl`（9 条已有记录，不重跑）

- [ ] **Step 1: 生成报告**

```bash
$PY scripts/perf_benchmark.py --report
```
预期输出包含（依据现有 9 条记录预先算得）：
- serial_nocache 行：`424s | 37204 | 24359 | ... | $0.0374`（q2 整行同源）
- 并行+缓存提速：`1.57x / 1.89x / 1.27x` → 中位 **1.57x**
- 再叠加分级：`1.86x / 1.43x / 1.08x` → 中位 **1.43x**
- 分级省钱：中位约 **1.13x**（≈ −12%）
- 结论行点明 parallel→tiered 延迟比值含劣化

- [ ] **Step 2: 人工核对新报告不再自相矛盾**

```bash
$PY -c "print(open('docs/reports/perf_20260813.md',encoding='utf-8').read())"
```
检查：serial 行的 token 与成本来自同一 run（37204 prompt ↔ \$0.0374），不再出现「token 更多却更便宜」。

- [ ] **Step 3: 删除旧的错误报告并 commit**

```bash
git rm docs/reports/perf_20260811.md
git add docs/reports/perf_20260813.md
git commit -m "docs: regenerate perf report with paired statistics (1.57x median, was mis-stated 1.66x)"
```

---

## Task 3: 索引类型可配置 + 防误配守卫

**Files:**
- Modify: `rag_pipeline.py`（第 49–51 行常量区、`_create_collection` 建索引处、`query()` 搜索参数处）
- Create: `tests/test_index_config.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_index_config.py`：

```python
"""
tests/test_index_config.py — 索引与检索参数的纯函数校验（无需 Milvus）。

背景：初版硬编码 IVF_FLAT + nlist=1024，而集合只有 161 个向量——1024 个
簇平均每簇 0.13 个向量，nprobe=64 仅搜 6.25% 的簇，会静默丢召回。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_pipeline import build_index_params, build_search_params, validate_index_for_size  # noqa: E402


def test_flat_index_has_no_nlist():
    p = build_index_params("FLAT")
    assert p["index_type"] == "FLAT"
    assert p["metric_type"] == "COSINE"
    assert p["params"] == {}


def test_flat_search_has_no_nprobe():
    assert build_search_params("FLAT") == {"metric_type": "COSINE", "params": {}}


def test_ivf_index_carries_nlist():
    p = build_index_params("IVF_FLAT", nlist=128)
    assert p["index_type"] == "IVF_FLAT"
    assert p["params"]["nlist"] == 128


def test_ivf_search_carries_nprobe():
    assert build_search_params("IVF_FLAT", nprobe=16)["params"]["nprobe"] == 16


def test_validate_rejects_nlist_above_entity_count():
    """就是当初 shipped 的那个误配：nlist=1024 vs 161 个向量。"""
    with pytest.raises(ValueError, match="nlist"):
        validate_index_for_size("IVF_FLAT", n_entities=161, nlist=1024)


def test_validate_accepts_flat_at_any_size():
    validate_index_for_size("FLAT", n_entities=161, nlist=1024)  # 不应抛异常


def test_validate_accepts_reasonable_ivf():
    validate_index_for_size("IVF_FLAT", n_entities=100_000, nlist=1024)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
$PY -m pytest tests/test_index_config.py -v
```
预期：`ImportError: cannot import name 'build_index_params' from 'rag_pipeline'`

- [ ] **Step 3: 在 `rag_pipeline.py` 常量区（`TOP_K = 5` 之后）插入配置与三个函数**

```python
# ── Vector index ──────────────────────────────────────────────────────────
# FLAT（穷举、精确）是万级以下向量的正确默认值：IVF 聚类只有在 nlist << N
# 时才有意义，nlist 超过实体数会留下大量空簇，受 nprobe 限制的搜索会静默
# 丢召回。本库当前 161 个向量，FLAT 既更准也更快。
INDEX_TYPE = os.getenv("MILVUS_INDEX_TYPE", "FLAT").upper()
IVF_NLIST  = int(os.getenv("MILVUS_IVF_NLIST", "128"))
IVF_NPROBE = int(os.getenv("MILVUS_IVF_NPROBE", "16"))


def build_index_params(index_type: str = None, nlist: int = None) -> dict:
    """构造 create_index 参数；FLAT 不带聚类参数。"""
    it = (index_type or INDEX_TYPE).upper()
    if it == "FLAT":
        return {"index_type": "FLAT", "metric_type": "COSINE", "params": {}}
    return {"index_type": it, "metric_type": "COSINE",
            "params": {"nlist": nlist if nlist is not None else IVF_NLIST}}


def build_search_params(index_type: str = None, nprobe: int = None) -> dict:
    """构造 search 参数；FLAT 无 nprobe 概念。"""
    it = (index_type or INDEX_TYPE).upper()
    if it == "FLAT":
        return {"metric_type": "COSINE", "params": {}}
    return {"metric_type": "COSINE",
            "params": {"nprobe": nprobe if nprobe is not None else IVF_NPROBE}}


def validate_index_for_size(index_type: str, n_entities: int, nlist: int = None) -> None:
    """nlist 超过实体数时抛错——这会让多数簇为空并静默损失召回。"""
    it = (index_type or "").upper()
    if it == "FLAT":
        return
    nl = nlist if nlist is not None else IVF_NLIST
    if nl > n_entities:
        raise ValueError(
            f"nlist={nl} 超过实体数 {n_entities}：多数簇将为空，受 nprobe 限制的"
            f"搜索会静默丢召回。此规模请用 MILVUS_INDEX_TYPE=FLAT。"
        )
```

- [ ] **Step 4: 把 `_create_collection` 里的硬编码索引替换掉**

将这段：
```python
        # Create IVF_FLAT index on embedding field
        index_params = {
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "params": {"nlist": 1024},
        }
        col.create_index(field_name="embedding", index_params=index_params)
        logger.info("Index created on 'embedding' field.")
```
替换为：
```python
        col.create_index(field_name="embedding", index_params=build_index_params())
        logger.info("Index created on 'embedding' field (%s).", INDEX_TYPE)
```

- [ ] **Step 5: 把 `query()` 里的硬编码搜索参数替换掉**

将 `search_params = {"metric_type": "COSINE", "params": {"nprobe": 64}}`
替换为 `search_params = build_search_params()`

- [ ] **Step 6: 在 `RAGPipeline` 类中新增 `rebuild_index` 方法（放在 `query` 方法之前）**

```python
    def rebuild_index(self) -> None:
        """就地重建向量索引（保留实体）。

        改动 MILVUS_INDEX_TYPE 后必须调用——索引只在集合首次创建时建立。
        """
        n = self.count()
        validate_index_for_size(INDEX_TYPE, n)
        self.collection.release()
        self.collection.drop_index()
        self.collection.create_index(field_name="embedding",
                                     index_params=build_index_params())
        self.collection.load()
        logger.info("Index rebuilt as %s over %d entities.", INDEX_TYPE, n)
```

- [ ] **Step 7: 跑测试确认通过**

```bash
$PY -m pytest tests/test_index_config.py -v
```
预期：`7 passed`

- [ ] **Step 8: Commit**

```bash
git add rag_pipeline.py tests/test_index_config.py
git commit -m "fix: default Milvus index to FLAT and guard nlist misconfiguration

IVF_FLAT with nlist=1024 over 161 vectors left ~0.13 vectors per cluster;
nprobe=64 searched 6.25% of clusters and silently lost recall."
```

---

## Task 4: 重建索引并重测检索（拿到诚实的 before/after）

**Files:**
- Create: `scripts/rebuild_index.py`
- Modify: `docs/reports/fact_eval_runs/retrieval_result.json`（由评测脚本覆写）

**前提：** Docker 中 Milvus 在线（`docker ps` 应见 `milvus-standalone ... (healthy)`；未起则 `docker compose up -d` 并等待 healthy）。

- [ ] **Step 1: 记录改造前的检索基线（IVF_FLAT，即当前线上索引）**

```bash
cp docs/reports/fact_eval_runs/retrieval_result.json /tmp/retrieval_before_ivf.json
$PY -c "import json;d=json.load(open('/tmp/retrieval_before_ivf.json',encoding='utf-8'));print('BEFORE(IVF_FLAT) hit@5=%s/%s  MRR=%s'%(d['hit_at_5'],d['total'],d['mrr']))"
```
预期：`BEFORE(IVF_FLAT) hit@5=29/30  MRR=0.847`

- [ ] **Step 2: 写重建脚本**

创建 `scripts/rebuild_index.py`：

```python
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
```

- [ ] **Step 3: 执行重建**

```bash
$PY scripts/rebuild_index.py
```
预期输出：
```
entities: 161
rebuilding index as FLAT ...
done
```

- [ ] **Step 4: 重跑检索评测**

```bash
$PY scripts/run_fact_eval.py --mode retrieval
```
预期末行形如：`hit@5: N/30 = X% | MRR: Y`（N ≥ 29；若为 30 则 IVF 误配确实吃掉了一个召回）

- [ ] **Step 5: 记录 before/after 对照**

```bash
$PY -c "
import json
b=json.load(open('/tmp/retrieval_before_ivf.json',encoding='utf-8'))
a=json.load(open('docs/reports/fact_eval_runs/retrieval_result.json',encoding='utf-8'))
print(f\"IVF_FLAT(nlist=1024,nprobe=64): hit@5 {b['hit_at_5']}/{b['total']}  MRR {b['mrr']}\")
print(f\"FLAT(exhaustive)             : hit@5 {a['hit_at_5']}/{a['total']}  MRR {a['mrr']}\")
miss_b={r['id'] for r in b['records'] if r['rank'] is None}
miss_a={r['id'] for r in a['records'] if r['rank'] is None}
print('IVF 漏检:', miss_b or '无', '| FLAT 漏检:', miss_a or '无')
"
```
把这段输出原样贴进 Task 6 的 checkpoint 记录。

- [ ] **Step 6: Commit**

```bash
git add scripts/rebuild_index.py docs/reports/fact_eval_runs/retrieval_result.json
git commit -m "test: rebuild index as FLAT and re-measure retrieval"
```

---

## Task 5: 验证 Layer-3 兜底是否真能回退（真实故障注入）

**背景：** `api_server.py:551` 确有回退分支，但 `tests/test_opt05_layer3_fallback.py` 用 `MagicMock` 跑 `_simulate_research_report`——只验证了分支逻辑，没验证 legacy 图能否真的产出可用答案。简历「三层降级验证测试 3/3 通过」目前撑不住追问。

**Files:**
- Create: `tests/test_layer3_real_fallback.py`

- [ ] **Step 1: 写真实故障注入测试**

创建 `tests/test_layer3_real_fallback.py`：

```python
"""
tests/test_layer3_real_fallback.py — Layer-3 兜底的真实故障注入测试。

与 test_opt05_layer3_fallback.py（mock 分支逻辑）不同，本测试让
run_deep_research 真正抛异常，验证 legacy 图能端到端产出非空答案——
即"最后一道防线"确实可用，而不只是代码里有这个 if。

需要 Milvus/Redis 在线；离线时自动 skip。
    python -m pytest tests/test_layer3_real_fallback.py -v -s
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.integration
def test_legacy_graph_produces_answer_when_deep_research_crashes(monkeypatch):
    try:
        import langgraph_agent
    except Exception as exc:
        pytest.skip(f"pipeline unavailable: {exc}")

    def _boom(*args, **kwargs):
        raise RuntimeError("forced crash for Layer-3 fault injection")

    monkeypatch.setattr(langgraph_agent, "run_deep_research", _boom)

    # 走与 api_server 相同的兜底路径：legacy 5-node 图
    try:
        graph = langgraph_agent.build_graph()
    except Exception as exc:
        pytest.skip(f"legacy graph unavailable: {exc}")

    state = {
        "question": "新型储能有哪些扶持政策？",
        "intent": "policy_query", "plan": [], "steps_executed": [],
        "reflection": "", "confidence": 0.0, "final_answer": "",
        "iteration": 0, "session_id": "layer3_fault_injection",
    }
    result = graph.invoke(state)
    answer = result.get("final_answer", "")
    print(f"\n[Layer3] legacy answer length = {len(answer)}")
    assert answer.strip(), "legacy 兜底返回空答案——最后一道防线实际不可用"
```

- [ ] **Step 2: 跑测试**

```bash
$PY -m pytest tests/test_layer3_real_fallback.py -v -s -m ""
```
两种结果都要如实记录：
- **PASS** → Task 6 中可写「Pipeline 级兜底经真实故障注入验证」
- **FAIL/SKIP** → Task 6 中必须把简历措辞降级为「已实现回退分支，单测覆盖分支逻辑」，**不得**声称验证通过

- [ ] **Step 3: 若 `build_graph` 名称不符导致 skip，先确认真实入口再改测试**

```bash
grep -n "^def build_graph\|^def build_research_graph" langgraph_agent.py
```
用实际存在的函数名替换 Step 1 中的 `build_graph`，重跑 Step 2。

- [ ] **Step 4: Commit**

```bash
git add tests/test_layer3_real_fallback.py
git commit -m "test: real fault-injection for Layer-3 legacy fallback"
```

---

## Task 6: 回填诚实表述到简历与文档

**Files:**
- Modify: `docs/interview-prep/resume_deepresearch_v3.md`
- Modify: `docs/interview-prep/resume_bullets_v2.md`
- Modify: `docs/checkpoints/interview-opt-checkpoint.md`

- [ ] **Step 1: 替换 `resume_deepresearch_v3.md` 中的描述行**

把「37 篇真实语料（政策全文/上市公司财报/市场统计，2021–2026）」中的 **上市公司财报** 改为 **上市公司业绩公告**，全句变为：

> 针对能源分析师跨政策/市场/财务多来源研究场景，独立设计并实现端到端 Multi-Agent 深度研究系统：37 篇真实语料（政策全文/上市公司业绩公告/市场统计，2021–2026）清洗入 Milvus 构成数据底座（138 chunks，检索 hit@5 96.7%），产出句级可审计的研究报告。

若 Task 4 的 FLAT 结果与 96.7% 不同，同步把该数字改为实测新值。

- [ ] **Step 2: 整条替换 Bullet 1（删除虚构因果链）**

把原 Bullet 1 整段替换为：

> **• Multi-Agent 架构演进与模型分级调度**：单 Agent ReAct 将规划/检索/写作/审查耦合在同一循环内，报告质量波动时无法定位失效环节；重构为六角色 LangGraph StateGraph 使每环可独立评测与替换，并用 asyncio 并行化检索与章节写作——单份报告耗时中位提速 1.57×（3 组固定 query，区间 1.27–1.89×）；大小模型分级路由再降 token 成本 12%（对延迟无稳定收益）。

改动要点：删掉「30 题全链路评测 28/30」（那是 wk4 五节点 pipeline 的成绩）与「失败例定位为 Router 意图误判」（该 2 例在 checkpoint 中记为 non-deterministic）；1.66× → 1.57×；明确分级只省钱不提速。

- [ ] **Step 3: 修 Bullet 3 的降级表述**

把「三层降级验证测试 3/3 通过」按 Task 5 的实际结果二选一：
- Task 5 PASS：改为「Pipeline 级兜底经真实故障注入验证可产出可用答案」
- Task 5 FAIL/SKIP：改为「三层均有对应测试（Pipeline 级为分支逻辑单测）」

- [ ] **Step 4: 修 Bullet 4 的基线表述**

把「top-1 平均相关度 0.68（基线 0.5）」改为：

> 跨 session 检索 top-1 平均相关度 0.68（自设 0.5 为可用阈值，3 条回归查询全部通过）

- [ ] **Step 5: 在 `resume_deepresearch_v3.md` 末尾的「有意省略」章节后追加追问预案**

```markdown
## 已知弱点与标准答法（面试前必读）

| 追问 | 事实 | 答法 |
|------|------|------|
| 27.5%→15.0% 显著吗？ | n=40，11 错 vs 6 错，Fisher 精确检验 **p=0.274** | 主动说明：方向性证据，样本量不足以下强结论，要定论需扩到 200 题以上 |
| hit@5 分母多少？题谁出的？ | 30 题，且题目对着语料出（evidence_quote 逐字锚定） | 承认存在 optimistic bias：它证明检索链路通，不代表绝对召回水平 |
| 9 万字为何不直接塞长上下文？ | 语料 ~13 万 token，1M 上下文确实塞得下 | 把论证从"规模"切到"可审计性"：chunk_id 是数字回指校验的锚点，全文塞入就没有锚点；并承认此规模下 RAG 的成本优势尚未体现 |
| 索引怎么选的？ | 初版照搬教程用 IVF_FLAT nlist=1024，161 个向量上每簇 0.13 个 | 讲成排查故事：发现 nprobe 只覆盖 6.25% 的簇 → 改 FLAT 重测（结果见 checkpoint） |
| 财报是解析 PDF 年报吗？ | 否，是财经媒体业绩报道稿，最短 209 字 | 如实说明是业绩公告与媒体报道；数字均可回溯到原文并逐字核对 |
```

- [ ] **Step 6: 同步英文版 `resume_bullets_v2.md`**

把该文件「性能/成本补充弹药」小节中的 `424s→304s→256s（1.66×）` 改为：

> 单份完整报告耗时：并行 + 缓存带来逐题配对提速中位 **1.57×**（区间 1.27–1.89×）；叠加大小模型分级后成本再降 **12%**，但延迟无稳定收益（3 条 query 中 2 条反而变慢）

- [ ] **Step 7: 在 `docs/checkpoints/interview-opt-checkpoint.md` 末尾追加修正记录**

```markdown
---

## 2026-08-13 事实性修正（面试前交叉验证）

| 项 | 修正前 | 修正后 | 根因 |
|---|---|---|---|
| 单报告提速 | 1.66× | **1.57×**（逐题配对中位，区间 1.27–1.89×） | `report()` 逐列独立取中位数，跨 run 拼接；1.66× 实为 q2 耗时 ÷ q0 耗时 |
| 模型分级 | 「累计提速」 | 仅降成本 12%，**延迟无稳定收益**（3 条 query 中 2 条变慢） | 同上，配对后暴露 |
| Milvus 索引 | IVF_FLAT nlist=1024 | **FLAT**（穷举精确） | 161 个向量上每簇 0.13 个，nprobe=64 仅覆盖 6.25% 的簇 |
| 检索指标 | hit@5 29/30, MRR 0.847 | 见 Task 4 实测输出 | 索引更换后重测 |
| Bullet 1 动机 | 「28/30，失败例定位为 Router 误判」 | 定性描述职责耦合导致失效环节不可定位 | 28/30 是 wk4 五节点 pipeline 成绩，2 例失败记为 non-deterministic，与 Router 误判（OPT-004）无记载因果 |
| 语料措辞 | 上市公司财报 | 上市公司业绩公告 | 实为财经媒体报道稿，最短 209 字 |
| MemGPT 基线 | 「基线 0.5」 | 「自设 0.5 为可用阈值」 | 非行业标准，系自设 |
| 统计口径 | 无 | 补 p=0.274 说明 | n=40 下 27.5% vs 15.0% 不显著 |
```

- [ ] **Step 8: 最终校验 —— 确认全仓不再残留旧数字**

```bash
grep -rn "1\.66\|上市公司财报\|基线 0\.5\|3/3 通过" docs/interview-prep/ docs/checkpoints/interview-opt-checkpoint.md
```
预期：无输出（若 Task 5 判定为 PASS，「3/3 通过」也应已被替换）。

- [ ] **Step 9: Commit**

```bash
git add docs/interview-prep/ docs/checkpoints/interview-opt-checkpoint.md
git commit -m "docs: correct resume claims to survive cross-verification

Remove fabricated causal chain in Bullet 1, restate speedup as 1.57x
paired median, soften corpus/baseline wording, add weakness playbook."
```

---

## Self-Review

**Spec 覆盖：** 六项问题 → Task 1–2（perf bug）、Task 3–4（索引）、Task 5（Layer-3 验证）、Task 6（Bullet 1 + 三处措辞 + 统计口径）全部有对应任务 ✓

**Placeholder 扫描：** 无 TBD/TODO；每个代码步骤都给出完整可粘贴代码；每条命令都有预期输出 ✓

**类型一致性：** `_median` / `_median_run` / `_paired_ratios` 在 Task 1 定义并在 Task 2 使用；`build_index_params` / `build_search_params` / `validate_index_for_size` / `rebuild_index` 在 Task 3 定义并在 Task 4 使用，签名一致 ✓

**已知风险：**
- Task 4 依赖 Milvus 在线；离线则整个任务阻塞（Task 1–2、6 不受影响，可先做）
- Task 5 可能失败——这是**预期内的信息收集**，失败时按 Task 6 Step 3 降级措辞，不得粉饰
- Task 4 若 FLAT 结果仍是 29/30，说明那次漏检与索引无关（应查 embedding 或评测标注），此时 Task 6 Step 1 的 96.7% 保持不变
