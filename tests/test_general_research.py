from backend.agents.chief_architect import run as plan_research
from backend.agents import data_analyst
from backend.agents import deep_scout
from backend.agents.data_analyst import _build_queries
from query_entry import classify_route


class EmptyPlanner:
    def chat_json(self, *_args, **_kwargs):
        return {"outline": [], "research_questions": []}


def test_research_fallback_does_not_insert_energy_sections():
    result = plan_research({"question": "Compare optical coherence tomography methods"}, EmptyPlanner())
    outline = " ".join(section["title"] + section["description"] for section in result["outline"])
    assert len(result["outline"]) >= 4
    assert "能源" not in outline
    assert "装机" not in outline
    assert "碳中和" not in outline
    assert result["hypotheses"] == []


def test_general_research_does_not_query_energy_database():
    state = {"question": "Compare optical coherence tomography methods",
             "intent": "research", "outline": [{"title": "数据分析", "keywords": ["OCT"]}]}
    assert _build_queries(state) == []


def test_general_research_skips_sql_execution(monkeypatch):
    calls = []

    def record_query(query):
        calls.append(query)
        return {"result": []}

    monkeypatch.setattr(data_analyst, "_run_text2sql", record_query)
    state = {"question": "Compare OCT methods", "intent": "research", "outline": []}
    assert data_analyst.run(state, None)["data_points"] == []
    assert calls == []


def test_english_literature_synthesis_uses_report_path():
    question = "How does OCT work? Write a structured literature synthesis using multiple studies."
    assert classify_route(question, lambda _q: {}).route == "multiagent"


def test_single_financial_fact_uses_answer_path_when_classifier_is_uncertain():
    question = "Which debt securities were registered under American Express in 2022?"
    assert classify_route(question, lambda _q: {"route": "uncertain"}).route == "react"


def test_literature_search_keeps_original_question_without_writing_instructions():
    question = ("How can OCT detect dynamic signals?\n\n"
                "Write a structured literature synthesis using multiple studies.")
    state = {"question": question,
             "research_questions": ["What is speckle variance in OCT?"]}
    assert deep_scout._search_queries(state) == [
        "How can OCT detect dynamic signals?", "What is speckle variance in OCT?"]
