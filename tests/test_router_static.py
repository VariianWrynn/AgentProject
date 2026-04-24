"""
Static assertions for OPT-004 router fix — no LLM call, no service dependencies.
Reads _ROUTER_SYSTEM directly from langgraph_agent.py source and checks structure.
"""

import ast
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "langgraph_agent.py")

# ---------------------------------------------------------------------------
# Extract _ROUTER_SYSTEM value by parsing the AST (no imports, no torch)
# ---------------------------------------------------------------------------

def _extract_router_system(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_ROUTER_SYSTEM":
                    if isinstance(node.value, ast.Constant):
                        return node.value.value
    raise AssertionError("_ROUTER_SYSTEM not found in langgraph_agent.py")


PASSED = []
FAILED = []

def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Run checks
# ---------------------------------------------------------------------------

prompt = _extract_router_system(SRC)

print("\n=== _ROUTER_SYSTEM static checks ===\n")

# 1. Tech-concept rule present
check("tech-concept rule: 'RAG' keyword",       "RAG" in prompt)
check("tech-concept rule: 'Embedding' keyword", "Embedding" in prompt)
check("tech-concept rule: 'VDB' keyword",       "VDB" in prompt)
check("tech-concept rule: 'LLM' keyword",       "LLM" in prompt)
check("tech-concept rule: '向量' keyword",       "向量" in prompt)
check("tech-concept rule: → research",          "→ research" in prompt)

# 2. Comparison rule present
check("comparison rule: '区别'",  "区别" in prompt)
check("comparison rule: '对比'",  "对比" in prompt)
check("comparison rule: 'compare'", "compare" in prompt)

# 3. Param/config rule present
check("param rule: 'Top-K'",     "Top-K" in prompt)
check("param rule: '阈值'",       "阈值" in prompt)
check("param rule: 'threshold'", "threshold" in prompt)
check("param rule: 'chunk'",     "chunk" in prompt)

# 4. Tiebreaker line present
check("tiebreaker: 优先选 research",
      "不确定时优先选 research" in prompt,
      "tiebreaker IMPORTANT line missing")

# 5. general narrowed to small talk only
check("general narrowed: '仅闲聊'", "仅闲聊" in prompt,
      "general should be restricted to small talk")

# 6. Original rules still present (no regression)
check("original rule: policy_query keywords", "政策" in prompt and "补贴" in prompt)
check("original rule: market_analysis keywords", "市场" in prompt and "规模" in prompt)
check("original rule: data_query keywords", "数据" in prompt and "多少" in prompt)

# 7. All five valid intents declared
for intent in ("policy_query", "market_analysis", "data_query", "research", "general"):
    check(f"intent declared: {intent}", intent in prompt)

# ---------------------------------------------------------------------------
# test_langgraph.py static checks
# ---------------------------------------------------------------------------

TEST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_langgraph.py")

print("\n=== test_langgraph.py static checks ===\n")

with open(TEST_FILE, encoding="utf-8") as f:
    test_src = f.read()

check('invalid intent "analysis" removed',
      '"analysis"' not in test_src,
      '"analysis" is still present — should have been changed to "research"')

check('intent seed is "__unset__" not "general"',
      '"__unset__"' in test_src,
      'seed should be "__unset__" to catch silent router failures')

check('guard assertion present',
      "__unset__" in test_src and "router never ran" in test_src,
      'guard assertion checking for "__unset__" not found')

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n{'='*40}")
print(f"Results: {len(PASSED)} PASS, {len(FAILED)} FAIL")
if FAILED:
    print("\nFailed checks:")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All checks passed.")
    sys.exit(0)
