"""Execute the approved flow through the real compiled LangGraph."""

from collections import Counter

import pytest

from backend import research_graph as research
from backend.agents.lead_writer import run as write
from backend.research_events import MemoryJournal, RunEvents, replay_state
from mcp_client import MCPCallError


EVIDENCE = {"E1": {"text": "营收123.4亿元。", "source": "annual.txt", "chunk_id": 0}}
GOOD = "营收123.4亿元[E1]。"
BAD = "营收999.9亿元[E1]。"


class Scenario:
    def __init__(self, *, score=0.96, rejected_drafts=0, decisions=()):
        self.calls = Counter()
        self.rounds = []
        self.score = score
        self.rejected_drafts = rejected_drafts
        self.decisions = iter(decisions)

    def router(self, state):
        return {"intent": "research"}

    def architect(self, state):
        self.calls["architect"] += 1
        return {"outline": [{"id": "s1", "title": "业绩"}], "phase": "researching"}

    def scout(self, state):
        self.calls["scout"] += 1
        self.rounds.append(state["iteration"])
        return {"evidence_frozen": EVIDENCE, "phase": "writing", "pending_queries": []}

    def writer(self, state):
        self.calls["writer"] += 1
        self.last_feedback = state["guard_feedback"]
        content = BAD if self.calls["writer"] <= self.rejected_drafts else GOOD
        return {"draft_sections": {"s1": content, "summary": content}, "phase": "reviewing"}

    def critic(self, state):
        self.calls["critic"] += 1
        assert state["gate_passed"] is True
        return {"quality_score": self.score, "critic_issues": [], "pending_queries": ["核实营收"]}

    def human(self, state):
        self.calls["human"] += 1
        decision = next(self.decisions)
        return {"user_decision": decision, "awaiting_human": False,
                "phase": "done" if decision in ("approve", "timeout") else "re_researching"}

    @property
    def nodes(self):
        return {"router": self.router, "chief_architect": self.architect,
                "deep_scout": self.scout, "lead_writer": self.writer,
                "critic_master": self.critic, "human_gate": self.human}


def execute(scenario, **kwargs):
    journal = MemoryJournal()
    events = RunEvents(journal, "session")
    state = research.run_deep_research("业绩如何", "session", events=events,
                                       nodes=scenario.nodes, **kwargs)
    return state, journal.read()


def test_evidence_failure_reenters_writer_and_gate_before_critic(monkeypatch):
    monkeypatch.setenv("FACT_GUARD", "off")
    scenario = Scenario(rejected_drafts=1)
    state, events = execute(scenario, fact_guard=False, demo_mode=True)
    assert scenario.calls == {"architect": 1, "scout": 1, "writer": 2, "critic": 1}
    assert scenario.last_feedback[0]["section"] == "s1"
    targets = [event["payload"]["target"] for event in events
               if event["type"] == "branch_selected" and event["node"] == "evidence_gate"]
    assert targets == ["lead_writer", "critic_master"]
    assert state["draft_sections"] == {"s1": GOOD, "summary": GOOD}
    assert GOOD in state["final_answer"]
    assert BAD not in state["final_answer"]
    assert replay_state(events) == state


def test_unresolved_evidence_never_enters_critic_or_assembly():
    scenario = Scenario(rejected_drafts=100)
    journal = MemoryJournal()
    with pytest.raises(research.EvidenceRejected):
        research.run_deep_research("业绩", events=RunEvents(journal, "sid"), nodes=scenario.nodes)
    assert scenario.calls["writer"] == 3
    assert scenario.calls["critic"] == 0
    assert not any(event["node"] == "synthesizer" for event in journal.read())
    assert journal.read()[-1]["type"] == "run_failed"


def test_automatic_research_runs_two_additional_rounds(monkeypatch):
    monkeypatch.setenv("HITL_ENABLED", "off")
    scenario = Scenario(score=0.4)
    state, events = execute(scenario)
    assert scenario.rounds == [0, 1, 2]
    assert scenario.calls["human"] == 0
    assert scenario.calls["critic"] == 3
    assert state["review_decision"] == "limit"
    assert replay_state(events) == state


def test_only_score_strictly_above_threshold_skips_human_review(monkeypatch):
    # 0.95 is NOT above the 0.95 threshold, so it must still reach the human gate.
    monkeypatch.setenv("HITL_ENABLED", "on")
    at_threshold = Scenario(score=0.95, decisions=["approve"])
    state, events = execute(at_threshold)
    assert at_threshold.calls["human"] == 1
    assert state["review_decision"] == "human"
    assert state["user_decision"] == "approve"
    assert replay_state(events) == state

    # Just above the threshold skips the human gate entirely.
    above = Scenario(score=0.96)
    state, events = execute(above)
    assert above.calls["human"] == 0
    assert state["review_decision"] == "approved"
    assert replay_state(events) == state


def test_at_threshold_with_hitl_off_researches_instead_of_skipping(monkeypatch):
    monkeypatch.setenv("HITL_ENABLED", "off")
    scenario = Scenario(score=0.95)
    state, events = execute(scenario)
    assert scenario.calls["human"] == 0
    assert scenario.rounds == [0, 1, 2]
    assert state["review_decision"] == "limit"
    assert replay_state(events) == state


def test_deep_scout_forwards_personal_key_to_search_tools(monkeypatch):
    captured = {}

    def fake_run(state, llm, tools=None):
        captured["api_key"] = tools.api_key
        return {}

    monkeypatch.setattr("backend.agents.deep_scout.run", fake_run)
    monkeypatch.setattr(research, "_llm", lambda role: object())

    class PersonalLLM:
        api_key = "visitor-personal-key"

    research.deep_scout_node({}, events=None, llm=PersonalLLM())
    assert captured["api_key"] == "visitor-personal-key"

    # Admin/local run (llm=None) forwards no personal key; search falls back to env.
    research.deep_scout_node({}, events=None, llm=None)
    assert captured["api_key"] is None


def test_human_rejection_uses_same_two_research_rounds(monkeypatch):
    monkeypatch.setenv("HITL_ENABLED", "on")
    scenario = Scenario(score=0.4, decisions=["reject", "reject"])
    state, events = execute(scenario)
    assert scenario.rounds == [0, 1, 2]
    assert scenario.calls["human"] == 2
    assert state["review_decision"] == "limit"
    assert replay_state(events) == state


@pytest.mark.parametrize("decision,scout_rounds,architect_calls", [
    ("chief_architect", [0, 1], 2), ("deep_scout", [0, 1], 1), ("lead_writer", [0], 1),
])
def test_human_intent_restarts_at_selected_node(monkeypatch, decision, scout_rounds, architect_calls):
    monkeypatch.setenv("HITL_ENABLED", "on")
    scenario = Scenario(score=0.4, decisions=[decision, "approve"])
    state, events = execute(scenario)
    assert scenario.calls["architect"] == architect_calls
    assert scenario.rounds == scout_rounds
    assert scenario.calls["writer"] == 2
    assert state["iteration"] == 1
    assert [event["payload"]["target"] for event in events
            if event["type"] == "branch_selected" and event["node"] == "human_gate"] == [decision, "synthesizer"]
    assert replay_state(events) == state


@pytest.mark.parametrize("decision", ["approve", "timeout"])
def test_human_approval_or_timeout_proceeds_without_research(monkeypatch, decision):
    monkeypatch.setenv("HITL_ENABLED", "on")
    scenario = Scenario(score=0.4, decisions=[decision])
    state, _ = execute(scenario)
    assert scenario.rounds == [0]
    assert state["user_decision"] == decision
    assert state["phase"] == "done"


def test_research_limit_never_bypasses_failed_evidence(monkeypatch):
    monkeypatch.setenv("HITL_ENABLED", "off")
    scenario = Scenario(score=0.4)
    original_writer = scenario.writer

    def writer(state):
        if state["iteration"] == 2:
            return {"draft_sections": {"s1": BAD, "summary": BAD}}
        return original_writer(state)

    nodes = {**scenario.nodes, "lead_writer": writer}
    with pytest.raises(research.EvidenceRejected):
        research.run_deep_research("业绩", nodes=nodes)
    assert scenario.calls["critic"] == 2


@pytest.mark.parametrize("error,expected", [
    (MCPCallError("rag_search", "offline"), research.ResearchExecutionError),
    (AssertionError("programmer error"), AssertionError),
])
def test_failed_node_does_not_publish_a_state_patch(error, expected):
    scenario = Scenario()
    journal = MemoryJournal()

    def failing_scout(state):
        state["facts"] = [{"content": "uncommitted mutation"}]
        raise error

    with pytest.raises(expected):
        research.run_deep_research("业绩", events=RunEvents(journal, "sid"),
                                   nodes={**scenario.nodes, "deep_scout": failing_scout})
    assert replay_state(journal.read())["facts"] == []
    assert not any(event["type"] == "state_updated" and event["node"] == "deep_scout"
                   for event in journal.read())


def test_domain_classification_cannot_exit_the_selected_research_path(monkeypatch):
    class Router:
        def chat_json(self, *args, **kwargs):
            return {"intent": "general"}

    monkeypatch.setattr(research, "_llm", lambda _: Router())
    assert research.router_node({"question": "研究问题"}) == {"intent": "research"}


def test_real_writer_feedback_is_driven_by_the_graph(monkeypatch):
    monkeypatch.setenv("PARALLEL", "off")
    scenario = Scenario()

    class LLM:
        def __init__(self):
            self.outputs = iter([BAD, BAD, GOOD, GOOD])
            self.prompts = []

        def chat(self, system, user, **kwargs):
            self.prompts.append(user)
            return next(self.outputs)

    llm = LLM()
    state = research.run_deep_research("业绩", nodes={**scenario.nodes,
        "lead_writer": lambda current: write(current, llm)})
    assert "校验反馈" in llm.prompts[2]
    assert state["draft_sections"]["s1"] == GOOD
    assert scenario.calls["critic"] == 1


def test_actual_human_gate_timeout_preserves_300_seconds(monkeypatch):
    import redis

    class Connection:
        def __init__(self):
            self.deleted = []

        def delete(self, key):
            self.deleted.append(key)

        def get(self, key):
            return None

    connection = Connection()
    monkeypatch.setattr(redis, "Redis", lambda **kwargs: connection)
    clock = iter([0, 301])
    monkeypatch.setattr(research.time, "monotonic", lambda: next(clock))
    events = RunEvents(MemoryJournal(), "sid", "run")
    state = research._make_initial_state("业绩", "sid", run_id="run")
    result = research.human_gate_node(state, events)
    assert research.HITL_TIMEOUT == 300
    assert result["user_decision"] == "timeout"
    assert connection.deleted == ["hitl_decision:run:0"]
    assert events.journal.read()[0]["payload"]["review_id"] == 0


def test_writer_keeps_other_sections_on_scoped_human_revision(monkeypatch):
    monkeypatch.setenv("PARALLEL", "off")
    prompts = []

    class Model:
        def chat(self, system, user, **kwargs):
            prompts.append(user)
            return "更新正文。"

    state = {"outline": [{"id": "s1", "title": "一"}, {"id": "s2", "title": "二"}],
             "draft_sections": {"s1": "原第一节。", "s2": "原第二节。"},
             "review_feedback": {"decision": "lead_writer", "section_ids": ["s2"], "feedback": "只改第二节"}}
    result = write(state, Model())
    assert result["draft_sections"] == {"s1": "原第一节。", "s2": "更新正文。", "summary": "更新正文。"}
    assert len(prompts) == 2
    assert "原第二节。" in prompts[0] and "只改第二节" in prompts[0]


def test_production_nodes_use_real_writer_critic_and_mcp_boundary(monkeypatch):
    import mcp_client

    calls = []

    class Response:
        def __init__(self, tool):
            self.tool = tool

        def raise_for_status(self):
            pass

        def json(self):
            hits = [{"source": "annual.txt", "content": "营收123.4亿元。", "score": 0.9,
                     "chunk_id": 0}] if self.tool == "rag_search" else []
            return {"result": hits}

    class LLM:
        def __init__(self, role):
            self.role = role

        def chat_json(self, system, user, **kwargs):
            calls.append((self.role, "json"))
            if self.role == "router":
                return {"intent": "research"}
            if self.role == "critic_master":
                return {"quality_score": 0.96, "issues": []}
            raise AssertionError(self.role)

        def chat(self, system, user, **kwargs):
            calls.append((self.role, "text"))
            return GOOD

    monkeypatch.setattr(research, "_llm", LLM)
    monkeypatch.setattr(mcp_client.requests, "post", lambda url, **kwargs: Response(url.rsplit("/", 1)[1]))
    journal = MemoryJournal()
    state = research.run_deep_research("业绩", demo_mode=True, events=RunEvents(journal, "sid"))
    assert ("critic_master", "json") in calls
    assert ("lead_writer", "text") in calls
    assert state["gate_passed"] is True
    assert state["references"][0]["label"] == "E1"
    assert GOOD in state["final_answer"]
    assert replay_state(journal.read()) == state
    assert {event["type"] for event in journal.read()} >= {"tool_started", "tool_completed"}


def test_critic_service_failure_is_not_a_successful_review(monkeypatch):
    from backend.agents.critic_master import run

    class LLM:
        def chat_json(self, *args, **kwargs):
            raise OSError("provider unavailable")

    with pytest.raises(OSError):
        run({"draft_sections": {"summary": "正文。"}, "demo_mode": True}, LLM())
