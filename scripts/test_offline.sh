#!/usr/bin/env bash
# 离线测试门禁 —— 提交前跑这个。
#
# 为什么是显式清单而不是 `pytest -m unit`：
#   pytest 会先 import 全部测试文件、再按 marker 过滤，而仓库里有若干文件在
#   导入期就需要 LLM/Milvus/MCP（构造客户端、读 OPENAI_API_KEY）。只要它们被
#   导入，收集阶段就报错，marker 过滤根本来不及生效。列出文件是唯一可靠的做法。
#
# 用法：
#   bash scripts/test_offline.sh
#
# 新增不依赖外部服务的测试文件时，把它加进下面的清单。

set -euo pipefail

PY="${PY:-python}"

OFFLINE_TESTS=(
  tests/test_citation_guard.py             # 引用回指校验（纯函数）
  tests/test_index_config.py               # Milvus 索引参数与 nlist 守卫
  tests/test_perf_report.py                # 性能报告统计口径
  tests/test_eval_set_integrity.py         # 评测集防编造校验
  tests/test_lead_writer_guard.py          # 事实约束写作回路（脚本化假 LLM）
  tests/test_text2sql_schema_retrieval.py  # 跨表 schema 选择
)

echo "离线门禁：${#OFFLINE_TESTS[@]} 个文件，不依赖 Milvus / Redis / MCP / LLM"
HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8 "$PY" -m pytest "${OFFLINE_TESTS[@]}" -q "$@"
