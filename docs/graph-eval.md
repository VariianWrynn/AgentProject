# ReAct 与 Multiagent 配对评测

## 评测口径

评测分成两个独立实验。中文能源业务实验包含 20 道问答题（8 事实、4 否定或方向、4 语料内不可回答、4 跨文档）和 8 道研究报告题。题目及证据在 `resources/data/eval/graph_comparison_v1.json`。生成脚本从经过原文校验的能源事实集中复制证据片段；所有引用都必须是 `resources/data/energy_corpus/clean` 内的原文子串。修改数据后运行 `validate` 和测试。

外部实验使用 [FinanceBench 官方开放样本](https://github.com/patronus-ai/financebench)的 20 道问答题，按信息抽取、数值推理、逻辑推理分成 8/8/4，覆盖不同企业。原始 150 条 JSONL 的 SHA-256 固定为 `a5a2aa673e573e55675fc3c0f9aa38c1cf59d2abc91edb077534f71f10a71877`。`build_financebench_eval.py` 验证原始文件后生成题目和 21 个证据页文本；这是**仅包含标注证据页**的检索实验，不能代表对完整财报的检索能力。英语金融问答结果与中文能源结果不能合成一个总分；FinanceBench 没有研究报告题，因此报告评分仍只用于能源实验。

两个实验都是**固定语料下的图编排对比**：能源实验使用 37 份文档，FinanceBench 实验使用 21 个标注证据页；每个实验内部两图共用一个只读检索器。当前英文查询使用词项 BM25，含中文的查询使用字符二元组；2026-10-20 以前的记录使用字符二元组，不能把不同检索器的成绩视为同配置复跑。评测运行器在进程内替换 Milvus、Redis、MCP、SQL 和网页搜索入口；它不测线上 Milvus 检索性能，也不等于生产环境端到端体验。每题新建进程与会话，语料文件哈希变化即失败。新图运行完整流程，`demo_mode=False`，`fact_guard=True`，关闭人工等待。现有新图的 `router` 仅给出领域标签，边固定进入 `chief_architect`，所以本实验衡量的是**强制完整报告路径**，不是“意图识别后自动选择短问答或报告”的系统。旧图的记忆判断 LLM 调用仍计入成本，但记忆存储被隔离。

## 评分

能源问答题由独立评审模型逐项核对实体、指标、年份、数值、单位、方向及证据支持。数字还须在答案中精确出现，或匹配题目预先列出的单位换算；出现数字和拒答词本身不能判对。不可回答题要求明确弃答，不能随后无据作答。FinanceBench 问答对照人工参考答案、理由及实际检索证据判定答案正确与证据支持，并额外要求命中标注证据页；报告还单列仅按参考答案判断的语义正确率，以区分答案错误和证据缺失。报告题逐条标记预设结论为有证据支持、矛盾、未提及或无证据，并另记额外的无证据结论数与任务覆盖率。图内 `quality_score` 不参与评分。两名人工评审使用 `export-review` 导出的问答和报告盲化样本独立核查；分歧另行复核，不覆盖原始标注。正式宣称评分可靠前，应先完成这一步。

评分规则参考 [FinanceBench](https://github.com/patronus-ai/financebench) 的答案与证据、[HotpotQA](https://github.com/hotpotqa/hotpot) 的支持事实，以及 [MuSiQue](https://github.com/stonybrooknlp/musique) 的多跳与证据充分性。公开集不进入首轮能源主榜。

## 运行

使用项目的 `agentPro` Python 环境，从仓库根目录执行：

```powershell
python scripts/graph_eval_runner.py validate
python -m pytest tests/test_graph_eval_core.py tests/test_graph_eval_runner.py -q
python scripts/graph_eval_runner.py run --jobs 4 --model deepseek-v4-pro --judge-model deepseek-flash --prices resources/data/eval/deepseek_prices_20261015.json
python scripts/graph_eval_runner.py report
python scripts/graph_eval_runner.py export-review
```

外部 FinanceBench 实验使用另一组 `--cases`、`--corpus` 和 `--out`，独立生成报告：

```powershell
python scripts/build_financebench_eval.py
python scripts/graph_eval_runner.py validate --cases resources/data/eval/external/financebench_pilot_v1.json --corpus resources/data/eval/external/financebench_pages_v1
python scripts/graph_eval_runner.py run --jobs 4 --cases resources/data/eval/external/financebench_pilot_v1.json --corpus resources/data/eval/external/financebench_pages_v1 --out docs/reports/graph_eval_runs/financebench.jsonl --model deepseek-v4-pro --judge-model deepseek-flash --prices resources/data/eval/deepseek_prices_20261015.json
python scripts/graph_eval_runner.py report --cases resources/data/eval/external/financebench_pilot_v1.json --corpus resources/data/eval/external/financebench_pages_v1 --out docs/reports/graph_eval_runs/financebench.jsonl --report-out docs/reports/graph_eval_runs/financebench_summary.md
```

首次获取公开文件可运行 `python scripts/build_financebench_eval.py --download`；之后构建仅使用本地 SHA-256 已校验的原始文件。续跑和报告按题集、语料、模型、评审模型、价格快照和并发数匹配配置，避免混用不同实验。`--jobs 4` 同时执行四道题，每道题仍先运行 ReAct 再运行 Multiagent；并发负载可能影响耗时，报告须记录这一条件。

`run` 会把题目、冻结语料片段和模型输出发送到配置的 LLM 服务，并产生费用。运行前确认服务目的地、模型名称、密钥和供应商价目表；评审模型必须与被测模型不同。可用 `--limit 1` 先检查一题配对，随后不带限制继续；已经评分的配对会跳过，失败项可以重试。默认每题每图最多等待 900 秒。原始 JSONL、Markdown 报告和盲评样本写入 `docs/reports/graph_eval_runs/`。

当前价格快照来自 [DeepSeek 官方价目表](https://api-docs.deepseek.com/quick_start/pricing/)（2026-10-15）。`cost_usd` 用峰时且输入缓存未命中的价格计算，是**费用上界估算**；现有 LLM 包装器未保存缓存命中 token，不能把它当供应商账单。缺失用量或单价时显示“无法核算”。评审模型 token 单独保存，不计入被测图费用。

首轮每图每题仅运行一次，结果用于发现评测集和评分器的问题。后续正式比较应在修订评分标准后对同一题重复运行，按题配对报告差值与不确定性，并保留原始失败记录。

## 公开长篇综合写作实验

若要评测实际 `/query` 分流入口，使用 `--candidate-mode routed` 并写入独立的 `--out` 文件。该模式逐题调用同一复杂度分类器；短问答进入 ReAct，明确要求文献综述或报告的问题进入研究图。报告中的候选列标为 `Routed`，原有 `Multiagent` 强制报告实验仍可复现。不要把两种模式的记录写入同一文件或合并成一个分数。

第三组独立实验取自 [ScholarQABench 的 ScholarQA-Multi](https://github.com/AkariAsai/ScholarQABench)。从专家撰写的跨论文长答案中固定选取 8 道题，分别覆盖计算机科学、生物物理和光子学。每题附加相同的“结构化文献综合、比较发现与局限、引用来源”要求；原问题保留在 `source_question`。从专家答案中抽取 5–8 条带单篇引用的结论，关联官方提供的论文片段；仅保留与被引片段有足够词项交集的结论，并通过原文子串校验。专家引用与词项交集只能证明出处可追溯，语义支持仍由独立评审模型和人工盲评核对。

语料是 8 题所附的 61 条论文片段，并非论文全文，也不代表开放网络检索。ReAct 与 Multiagent 共用这份冻结语料，按结论支持覆盖率、错误必需结论率、无证据结论率、任务覆盖率、输入/输出 token、费用和耗时配对比较。报告评分沿用本项目规则，是对公开集的**改编评测**，不等同 ScholarQABench 官方排行榜指标，也不与中文能源或 FinanceBench 得分混算。两图都用 `deepseek-flash`，独立评分用 `deepseek-v4-pro`；价格快照按 2026-10-18 官方峰时缓存未命中价格记录。评审模型费用不计入图的推理费用。

```powershell
python scripts/build_scholarqa_report_eval.py
python scripts/graph_eval_runner.py validate --cases resources/data/eval/external/scholarqa_multi_reports_v1.json --corpus resources/data/eval/external/scholarqa_multi_snippets_v1
python scripts/graph_eval_runner.py run --jobs 4 --cases resources/data/eval/external/scholarqa_multi_reports_v1.json --corpus resources/data/eval/external/scholarqa_multi_snippets_v1 --out docs/reports/graph_eval_runs/scholarqa_multi_flash_live.jsonl --model deepseek-flash --judge-model deepseek-v4-pro --prices resources/data/eval/deepseek_prices_20261018.json
python scripts/graph_eval_runner.py report --cases resources/data/eval/external/scholarqa_multi_reports_v1.json --corpus resources/data/eval/external/scholarqa_multi_snippets_v1 --out docs/reports/graph_eval_runs/scholarqa_multi_flash_live.jsonl --model deepseek-flash --judge-model deepseek-v4-pro --prices resources/data/eval/deepseek_prices_20261018.json --report-out docs/reports/graph_eval_runs/scholarqa_multi_flash_summary.md
python scripts/graph_eval_runner.py export-review --cases resources/data/eval/external/scholarqa_multi_reports_v1.json --corpus resources/data/eval/external/scholarqa_multi_snippets_v1 --out docs/reports/graph_eval_runs/scholarqa_multi_flash_live.jsonl --model deepseek-flash --judge-model deepseek-v4-pro --prices resources/data/eval/deepseek_prices_20261018.json --review-out docs/reports/graph_eval_runs/scholarqa_multi_flash_blind_review.json
```

官方原始文件固定 SHA-256 为 `83af055a691a8a0be078cb2a02813193a3fc2966091b28bd51824b43049fffea`。若本地缺失，用 `python scripts/build_scholarqa_report_eval.py --download` 获取，再构建题集；不要修改原始文件或语料后沿用旧成绩。ScholarQABench 的数据按其仓库说明以 [ODC-BY](https://github.com/AkariAsai/ScholarQABench#license) 发布，使用时保留来源归属。

## 2026-10-20 分流与检索修正复测

英文固定语料检索改为 BM25；报告证据按来源优先冻结，再保留同来源的额外段落，且不截断检索返回的片段。明确的英文短问答即使涉及多个年份或来源，也走 ReAct；文献综述和结构化报告仍走研究图。独立重跑后，FinanceBench 分流候选 20/20 可评分配对，证据约束正确 16/20（80%），20 题均实际走 ReAct。ScholarQA 8/8 可评分配对，分流候选的有证据支持必需结论覆盖率 61.3%，必需证据文档召回 42/53；同轮 ReAct 覆盖率 66.5%。这轮与之前的字符二元组检索试跑配置不同，分数差异不能全部归于提示词或报告图。样本量小、每题只运行一次，自动评分仍需人工盲评；以上不等于官方排行榜成绩。
