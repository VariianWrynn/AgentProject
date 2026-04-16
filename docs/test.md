SECTION 1 — RAG质量 + ReAct多轮行动（10题）
按以下问题列表依次执行，每题通过LangGraph完整链路运行：
VDB文档5题：

VDB-1（factual）：VectorDB Pro当前最新稳定版本是什么？该版本是何时发布的？关键词验证：["3.2", "2024年9月15日"]
VDB-2（negation）：VectorDB Pro v3.2是否仍然支持ANNOY索引？必须包含["不支持","废弃"]，禁止出现["支持ANNOY"]
VDB-3（multi-step，期望steps≥2）：我需要为生产系统选择索引。请告诉我v3.2 HNSW和IVF-PQ的P99延迟分别是多少？QPS分别是多少？内存占用分别是多少？给出选择建议。关键词验证：["4.2","6.8","142,000","98,000","82","31"]
VDB-4（unanswerable）：VectorDB Pro v3.2的月度订阅价格是多少？必须包含["未提及","无法","文档中没有"]之一，answer中出现任何价格数字则强制FAIL
VDB-5（conflict + multi-step，期望steps≥2）：请先告诉我VectorDB Pro v3.1的Collection数量限制，再告诉我v3.2各版本的限制，并说明两处描述是否存在矛盾。关键词验证：["65,536","10,000"]，answer需同时出现两个数字

HR文档5题：

HR-1（negation）：工龄奖励假目前是否仍然有效？工龄满5年的员工能否申请？必须包含["取消","废止","不再"]，禁止出现["可以申请","满5年享有"]
HR-2（table + conditional）：一名连续工龄恰好满8年的员工，每年享有多少天年假？必须包含["20"]，禁止出现["15天"]
HR-3（numerical + multi-step，期望steps≥2）：帮我计算年终奖：员工4月15日入职，绩效A，月薪20000元。请先告诉我A级系数，再说明不足12个月的规则，最后给出金额。关键词验证：["45,000","3","9/12"]
HR-4（conditional + negation）：员工提交离职申请后，未使用年假补偿比例是150%还是100%？必须包含["100%"]，禁止出现["150%"]
HR-5（multi-step，期望steps≥2）：试用期员工连续请了12个工作日病假。请分三步回答：带薪上限多少天？超出如何处理？公司有权解除合同吗？关键词验证：["3天","无薪","10个工作日"]

评分规则：

expected_keywords全部出现且无forbidden_keywords → PASS
expected_keywords部分出现 → PARTIAL
forbidden_keywords出现 → FAIL
对multi-step题目额外检查：len(steps_executed) >= expected_min_steps，否则标注WARN

每题打印格式：
VDB-3 [numerical,table,multi-step] — PASS (steps=3)
  Answer前200字: ...
  RAG chunks: 4  最高相似度: 0.834
  工具调用: [rag_search, rag_search, rag_search]
  Reflector decisions: [continue, continue, done]
  耗时: 14.2s

SECTION 2 — MemGPT记忆写入 + 更新测试（6个）
MEM-1 Core Memory首次写入：

问题："我是一名数据工程师，主要负责华北地区的业务，对IVF-PQ索引特别感兴趣，以后的分析都聚焦这个方向"
验证：Core Memory human block包含["数据工程师","华北","IVF-PQ"]，打印写入后的human block

MEM-2 Archival Memory写入验证：

问题："帮我查询华北地区上个季度所有产品类别的销售总额，并总结哪个类别表现最好"
验证：archival_memory_insert被触发，打印插入的内容

MEM-3 跨session Archival检索（依赖MEM-2先执行）：

新session，问题："上次分析华北地区销售的结论是什么？"
验证：archival_memory_search被触发，top1相似度≥0.5，打印检索到的内容

MEM-4 Core Memory更新（依赖MEM-1先执行）：

问题："我换岗位了，现在负责华南区域，不再关注IVF-PQ了，改为研究HNSW调优"
验证：human block更新后包含["华南","HNSW"]，打印更新前后的human block对比

MEM-5 Core Memory FIFO上限：

直接调用core_memory_append 8次，每次写入250字符内容
验证：最终human block长度≤2000，最新写入内容存在，最早写入内容已被截断
打印每次写入后的block长度

MEM-6 记忆注入影响Planner决策：

预先注入human block："用户是销售分析师，偏好用text2sql查询结构化数据"
问题："帮我分析一下最近的销售情况"
验证：Planner的system prompt前300字包含注入内容，tools_used包含text2sql

每个测试打印：
MEM-4 [Core Memory更新] — PASS
  更新前 human block: 数据工程师、华北、IVF-PQ... (87 chars)
  更新后 human block: 华南区域、HNSW调优... (72 chars)
  memory_action: core_memory_replace

SECTION 3 — RAG + MemGPT融合测试（5个）
FUS-1 RAG结论自动归档：

问题："详细解释VectorDB Pro中HNSW和IVF-PQ各自的适用场景以及核心性能差异"
验证：rag_search被触发，执行后archival_memory_insert被触发，打印归档内容

FUS-2 Archival记忆增强RAG（依赖FUS-1）：

新session，问题："基于我们之前讨论的索引知识，如果我的系统有128GB内存，应该选哪种索引？"
验证：archival_memory_search和rag_search都被触发，steps≥2，answer中有引用历史内容的表述

FUS-3 跨文档多轮推理（VDB + HR混合知识库）：

问题："VectorDB Pro专业版的数据保留期限是多久？另外，公司员工的带薪病假是多少天？"
验证：answer同时包含["180天","12天"]，RAG召回chunks来自两个不同source文件

FUS-4 三路协同（Core Memory + RAG + Text2SQL）：

预注入human block："用户是华南区销售总监，关注电子产品业务，同时在研究向量数据库选型"
问题："给我一个综合报告：华南区电子产品的销售数据怎么样，以及HNSW索引的内存要求是多少"
验证：tools_used同时包含["text2sql","rag_search"]，Planner steps≥2，Core Memory出现在planner prompt中

FUS-5 记忆更新影响路由（AB对照）：

5a：无偏好注入，问"帮我分析一下数据库相关的内容"，记录tools_used_5a
5b：注入human="用户只关心结构化销售数据，所有数据库问题都用SQL查询"，问同样问题，记录tools_used_5b
验证：tools_used_5a ≠ tools_used_5b，打印两次对比，人工确认差异合理


最终汇总报告：
============================================================
RAG + MemGPT 全链路测试报告
============================================================
SECTION 1 — RAG质量 + ReAct多轮行动
  VDB-1 [factual]                    PASS    8.1s
  VDB-2 [negation]                   PASS    7.3s
  VDB-3 [numerical,multi-step]       PASS    14.2s  steps=3
  VDB-4 [unanswerable]               PASS    6.8s
  VDB-5 [conflict,multi-step]        PARTIAL 12.1s  steps=2  # 缺少冲突分析
  HR-1  [negation]                   PASS    7.9s
  HR-2  [table,conditional]          FAIL    6.2s   # 错误返回15天
  HR-3  [numerical,multi-step]       PASS    16.3s  steps=3
  HR-4  [conditional,negation]       PASS    8.4s
  HR-5  [multi-step]                 PASS    15.1s  steps=2
  ─────────────────────────────────────
  RAG得分: X/10 (PASS) Y/10 (PARTIAL) Z/10 (FAIL)
  multi-step题目平均steps: X.X
  按维度统计:
    negation:    X/3
    multi-step:  X/3 (期望steps达标)
    table:       X/2
    unanswerable:X/1
    conflict:    X/1

SECTION 2 — MemGPT记忆测试
  MEM-1 Core Memory首次写入          PASS
  MEM-2 Archival写入验证             PASS
  MEM-3 跨session检索                PASS
  MEM-4 Core Memory更新              PASS
  MEM-5 FIFO上限                     PASS
  MEM-6 记忆注入影响Planner          PASS
  ─────────────────────────────────────
  记忆测试: X/6 PASS

SECTION 3 — 融合测试
  FUS-1 RAG结论自动归档              PASS
  FUS-2 Archival增强RAG              PASS
  FUS-3 跨文档多轮推理               PASS
  FUS-4 三路协同                     PASS
  FUS-5 路由变化对照                 PASS
  ─────────────────────────────────────
  融合测试: X/5 PASS

总体: XX/21
平均响应时间: X.Xs
============================================================
运行完把完整输出发给我。