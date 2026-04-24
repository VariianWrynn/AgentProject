"""
OPT-04 static tests: router misclassification fix
Uses ast.parse() to extract _ROUTER_SYSTEM and check test_langgraph.py changes.
No torch / LLM / server required.
"""
import ast
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status}  {name}{suffix}")


# ─────────────────────────────────────────────────────────────────────────────
# Extract _ROUTER_SYSTEM from langgraph_agent.py via AST (no import)
# ─────────────────────────────────────────────────────────────────────────────
agent_path = os.path.join(ROOT, "langgraph_agent.py")
with open(agent_path, encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source)
router_system = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "_ROUTER_SYSTEM":
                if isinstance(node.value, (ast.Constant, ast.JoinedStr)):
                    router_system = ast.literal_eval(node.value) if isinstance(node.value, ast.Constant) else None
                # Handle implicit string concatenation
                if isinstance(node.value, ast.Constant):
                    router_system = node.value.value

if router_system is None:
    # Try getting it as source text fallback
    import re
    m = re.search(r'_ROUTER_SYSTEM\s*=\s*"""(.*?)"""', source, re.DOTALL)
    if m:
        router_system = m.group(1)

print(f"=== OPT-04: Router Static Checks ===\n")
print(f"-- Part 1: _ROUTER_SYSTEM new rules --")

rs = router_system or ""

# ── New tech-concept rule ──────────────────────────────────────────────────
check("tech rule: 'RAG' keyword present",      "RAG" in rs)
check("tech rule: 'Embedding' keyword present", "Embedding" in rs or "嵌入" in rs)
check("tech rule: 'VDB' keyword present",       "VDB" in rs or "Vector Database" in rs)
check("tech rule: 'LLM' keyword present",       "LLM" in rs)
check("tech rule: '向量' keyword present",      "向量" in rs)
check("tech rule: maps to → research",          "research" in rs)

# ── New comparison rule ────────────────────────────────────────────────────
check("comparison rule: '区别' present",    "区别" in rs)
check("comparison rule: '对比' present",    "对比" in rs)
check("comparison rule: 'compare' present", "compare" in rs)

# ── New parameter/config rule ──────────────────────────────────────────────
check("param rule: 'Top-K' present",       "Top-K" in rs)
check("param rule: '阈值' present",        "阈值" in rs)
check("param rule: 'threshold' present",   "threshold" in rs)
check("param rule: 'chunk' present",       "chunk" in rs)

# ── Tiebreaker IMPORTANT line ──────────────────────────────────────────────
check("tiebreaker: 'IMPORTANT' + '优先选 research' line present",
      "优先选 research" in rs or "优先选research" in rs)

# ── general narrowed to small talk ────────────────────────────────────────
check("general narrowed: '仅闲聊' restricts general to small talk",
      "闲聊" in rs or "寒暄" in rs)

# ── Original rules preserved ──────────────────────────────────────────────
print("\n-- Part 2: original rules preserved --")
check("original: policy_query rule still present", "policy_query" in rs)
check("original: market_analysis rule still present", "market_analysis" in rs)
check("original: data_query rule still present", "data_query" in rs)

# ── All 5 intents declared ─────────────────────────────────────────────────
for intent in ["policy_query", "market_analysis", "data_query", "research", "general"]:
    check(f"intent declared in output spec: {intent}", intent in rs)


# ─────────────────────────────────────────────────────────────────────────────
# Static checks on tests/test_langgraph.py
# ─────────────────────────────────────────────────────────────────────────────
print("\n-- Part 3: test_langgraph.py fixes --")
tl_path = os.path.join(ROOT, "tests", "test_langgraph.py")
with open(tl_path, encoding="utf-8") as f:
    tl_src = f.read()

check("invalid intent 'analysis' removed",
      '"analysis"' not in tl_src,
      "'analysis' still present" if '"analysis"' in tl_src else "absent")

check("intent seed is '__unset__' (not 'general')",
      '"__unset__"' in tl_src,
      "found '__unset__'" if '"__unset__"' in tl_src else "missing")

check("guard assertion checking '__unset__' present",
      "__unset__" in tl_src and "router never ran" in tl_src)

check("expected_intent for doc-topics question is 'research'",
      '"expected_intent": "research"' in tl_src or
      "'expected_intent': 'research'" in tl_src)


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
total  = len(results)
print(f"\nResults: {passed}/{total}")
sys.exit(0 if passed == total else 1)
