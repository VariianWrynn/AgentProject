"""Build the frozen, traceable two-graph pilot set from verified energy facts."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "resources/data/eval/energy_eval_set.json"
OUTPUT = ROOT / "resources/data/eval/graph_comparison_v1.json"

# Metadata is reviewed against the source quote; source text is copied verbatim.
FIELDS = {
    "fact-001": ("宁德时代", "归母净利润", "2023", "441.21", "亿元", "增长"),
    "fact-002": ("宁德时代", "营业总收入", "2024", "3620.13", "亿元", "下降"),
    "fact-003": ("阳光电源", "收入", "2023", "722.5", "亿元", "增长"),
    "fact-004": ("阳光电源", "归母净利润", "2024", "110.36", "亿元", "增长"),
    "fact-005": ("隆基绿能", "营业收入", "2023", "1294.98", "亿元", "增长"),
    "fact-006": ("金风科技", "营业收入", "2023", "504.57", "亿元", "增长"),
    "fact-007": ("亿纬锂能", "营业收入", "2023", "487.84", "亿元", "增长"),
    "fact-008": ("长江电力", "六座梯级电站发电量", "2023", "2762.63", "亿千瓦时", "增长"),
    "fact-009": ("全国", "全社会用电量", "2024", "98521", "亿千瓦时", "增长"),
    "fact-010": ("全国", "新型储能累计装机", "2024", "7376", "万千瓦", "增长"),
    "fact-011": ("全国", "动力电池累计装车量", "2024", "548.4", "GWh", "增长"),
    "fact-012": ("全国", "风电新增吊装容量", "2023", "7937", "万千瓦", "增长"),
    "fact-013": ("中国", "风电太阳能总装机目标", "2030", "12", "亿千瓦", "达到"),
    "fact-014": ("中国", "非化石能源消费比重目标", "2025", "20", "%", "达到"),
    "fact-015": ("中华人民共和国能源法", "施行日期", "2025", "2025", "年1月1日", "生效"),
    "fact-016": ("全国碳市场", "配额年成交额", "2024", "181.14", "亿元", "创高"),
    "fact-017": ("中国", "新型储能累计装机", "2024", "78.3", "GW", "增长"),
    "fact-018": ("国内", "多晶硅产量", "2024年11月", "13.3", "万吨", "下降"),
    "fact-019": ("全球", "光伏新增装机", "2023", "444", "GW", "增长"),
    "fact-020": ("中国神华", "营业收入", "2022", "3445.33", "亿元", "增长"),
    "neg-001": ("金风科技", "净利润", "2023", "13.31", "亿元", "下降"),
    "neg-002": ("大全能源", "营业总收入", "2023", "163.29", "亿元", "下降"),
    "neg-003": ("宁德时代", "营业总收入同比", "2024", "9.70", "%", "下降"),
    "neg-005": ("多晶硅n型复投料", "成交均价", "2024年12月", "4.03", "万元/吨", "下降"),
    "neg-006": ("跨区外送通道", "可再生能源电量比例", "2030年前碳达峰行动方案", "50", "%", "不低于"),
    "neg-007": ("金风科技", "经营活动现金流量净额", "2023", "18.54", "亿元", "下降"),
    "neg-008": ("中国神华", "煤炭销量", "2022", "4.178", "亿吨", "下降"),
    "neg-009": ("隆基绿能", "营业成本", "2023", "1058.52", "亿元", "下降"),
    "neg-010": ("通威股份", "第三季度亏损", "2024", "8", "亿元", "亏损"),
}

CONVERSIONS = {
    "fact-010": [{"value": "73.76", "unit": "GW"}],
    "fact-011": [{"value": "0.5484", "unit": "TWh"}],
    "fact-013": [{"value": "1200", "unit": "GW"}],
    "fact-018": [{"value": "133000", "unit": "吨"}],
    "fact-019": [{"value": "4.44", "unit": "亿千瓦"}],
}

QA_FACT = ["fact-001", "fact-004", "fact-008", "fact-009", "fact-010", "fact-011", "fact-013", "fact-014"]
QA_NEG = ["neg-001", "neg-003", "neg-005", "neg-006"]
QA_UN = ["un-001", "un-002", "un-005", "un-008"]
MULTIHOP = [
    ("比较宁德时代和亿纬锂能2023年的归母净利润与营业收入，分别是多少？", ["fact-001", "fact-007"]),
    ("2024年全国全社会用电量和新型储能累计装机分别是多少？", ["fact-009", "fact-010"]),
    ("碳达峰行动方案的2030年风光装机目标是多少，能源法何时施行？", ["fact-013", "fact-015"]),
    ("2024年11月国内多晶硅产量与2023年全球光伏新增装机分别是多少？", ["fact-018", "fact-019"]),
]
REPORTS = [
    ("政策", "梳理碳达峰行动方案与能源法中有据可查的能源目标和实施节点。", ["fact-013", "fact-014", "fact-015", "neg-006", "fact-010"]),
    ("政策", "分析中国储能发展规模及政策目标，区分不同统计口径。", ["fact-010", "fact-017", "fact-013", "fact-014", "fact-015"]),
    ("政策", "概述碳达峰背景下的风光、电力消费和碳市场数据。", ["fact-013", "fact-014", "neg-006", "fact-009", "fact-016"]),
    ("财务", "对比宁德时代与亿纬锂能的2023年业绩，并说明宁德时代2024年变化。", ["fact-001", "fact-002", "neg-003", "fact-007", "fact-003"]),
    ("财务", "比较阳光电源、隆基绿能和大全能源的已知财务表现。", ["fact-003", "fact-004", "fact-005", "neg-009", "neg-002"]),
    ("财务", "梳理金风科技与中国神华已披露的营收、利润及经营数据。", ["fact-006", "neg-001", "neg-007", "fact-020", "neg-008"]),
    ("市场", "分析2024年中国储能和动力电池市场的已知规模数据。", ["fact-010", "fact-017", "fact-011", "fact-009", "fact-016"]),
    ("市场", "梳理光伏和多晶硅市场的已知产量、装机与价格变化。", ["fact-018", "fact-019", "neg-005", "neg-010", "fact-012"]),
]


def build() -> list[dict]:
    source = {item["id"]: item for item in json.loads(SOURCE.read_text(encoding="utf-8"))}

    def claim(ref: str, index: int) -> dict:
        item = source[ref]
        fields = FIELDS[ref]
        return {"id": f"c{index}", "source_case": ref,
                **dict(zip(("entity", "metric", "period", "value", "unit", "direction"), fields)),
                "allowed_conversions": CONVERSIONS.get(ref, []),
                "evidence_doc": item["evidence_doc"], "evidence_quote": item["evidence_quote"]}

    cases = []
    for category, refs in (("factual", QA_FACT), ("negation", QA_NEG), ("unanswerable", QA_UN)):
        for ref in refs:
            item = source[ref]
            question = ("2024年12月多晶硅n型复投料成交均价环比上涨了吗？均价和变动幅度是多少？"
                        if ref == "neg-005" else item["question"])
            case_claims = [] if category == "unanswerable" else [claim(ref, 1)]
            if ref == "neg-001":
                rate = claim(ref, 2)
                rate.update(metric="净利润同比变化", value="44.16", unit="%")
                case_claims.append(rate)
            if ref == "neg-005":
                rate = claim(ref, 2)
                rate.update(metric="成交均价环比变化", value="0.49", unit="%")
                case_claims.append(rate)
            cases.append({"id": ref, "track": "qa", "category": category,
                          "question": question, "gold_answer": item["ground_truth"],
                          "answerable": category != "unanswerable",
                          "claims": case_claims})
    for index, (question, refs) in enumerate(MULTIHOP, 1):
        cases.append({"id": f"mh-{index:03}", "track": "qa", "category": "multihop",
                      "question": question, "gold_answer": "；".join(source[r]["ground_truth"] for r in refs),
                      "answerable": True, "claims": [claim(r, i) for i, r in enumerate(refs, 1)]})
    for index, (topic, question, refs) in enumerate(REPORTS, 1):
        cases.append({"id": f"report-{index:03}", "track": "report", "category": topic,
                      "question": question, "answerable": True,
                      "claims": [claim(r, i) for i, r in enumerate(refs, 1)],
                      "forbidden_inference": "不得把相关性写成因果关系，或把不同年份、统计口径的数据直接相减。"})
    return cases


if __name__ == "__main__":
    cases = build()
    OUTPUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {OUTPUT}")
