from backend.agents.chief_architect import run


def test_concise_plan_is_not_expanded_into_process_chapters():
    outline = [{"id": "results", "title": "营收排名", "description": "营收排名与必要限制", "keywords": ["营收"]}]

    class Planner:
        def chat_json(self, *_args, **_kwargs):
            return {"outline": outline, "hypotheses": [], "research_questions": ["营收排名"]}

    result = run({"question": "简短列出三家企业营收", "intent": "data_query"}, Planner())
    assert result["outline"] == outline
    assert len(result["outline"]) == 1
