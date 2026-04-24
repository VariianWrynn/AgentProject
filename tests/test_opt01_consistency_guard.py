"""
OPT-01 unit tests: CriticMaster _consistency_guard()
No LLM / torch / heavy imports required.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents.critic_master import _consistency_guard

results = []

def check(name, got, expected):
    ok = abs(got - expected) < 1e-9
    results.append((name, ok, got, expected))

# Rule 1: high-severity issue + score > 0.7 -> capped at 0.65
issues_high = [{"severity": "high", "type": "hallucination"}]
check("Rule1: high+0.85 -> 0.65",     _consistency_guard(issues_high, 0.85), 0.65)
check("Rule1: high+0.72 -> 0.65",     _consistency_guard(issues_high, 0.72), 0.65)
check("Rule1: high+0.70 -> unchanged (boundary: > not >=)", _consistency_guard(issues_high, 0.70), 0.70)
check("Rule1: high+0.60 -> unchanged", _consistency_guard(issues_high, 0.60), 0.60)
check("Rule1: high+0.65 -> unchanged", _consistency_guard(issues_high, 0.65), 0.65)

# Rule 2: any issues + score > 0.85 -> capped at 0.85 (no high severity)
issues_low = [{"severity": "low", "type": "incomplete"}]
check("Rule2: low+0.90  -> 0.85",     _consistency_guard(issues_low, 0.90), 0.85)
check("Rule2: low+0.86  -> 0.85",     _consistency_guard(issues_low, 0.86), 0.85)
check("Rule2: low+0.85  -> unchanged", _consistency_guard(issues_low, 0.85), 0.85)
check("Rule2: low+0.80  -> unchanged", _consistency_guard(issues_low, 0.80), 0.80)

# Rule 1 takes precedence when both would apply
issues_mixed = [{"severity": "high"}, {"severity": "low"}]
check("Precedence: high+0.95 -> 0.65 (not 0.85)", _consistency_guard(issues_mixed, 0.95), 0.65)

# No issues: always pass through unchanged
check("No issues: 0.90 -> 0.90", _consistency_guard([], 0.90), 0.90)
check("No issues: 0.50 -> 0.50", _consistency_guard([], 0.50), 0.50)

# Medium severity only (no high): Rule 2 applies if > 0.85
issues_med = [{"severity": "medium"}]
check("medium+0.88 -> 0.85", _consistency_guard(issues_med, 0.88), 0.85)
check("medium+0.70 -> unchanged", _consistency_guard(issues_med, 0.70), 0.70)

# Edge: multiple high issues
issues_multi_high = [{"severity": "high"}, {"severity": "high"}]
check("2x high+0.80 -> 0.65", _consistency_guard(issues_multi_high, 0.80), 0.65)

passed = sum(1 for _, ok, _, _ in results if ok)
total  = len(results)

print("=== OPT-01: CriticMaster _consistency_guard() unit tests ===")
for name, ok, got, exp in results:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}  (got={got:.4f}, expected={exp:.4f})")
print(f"\nResults: {passed}/{total}")
sys.exit(0 if passed == total else 1)
