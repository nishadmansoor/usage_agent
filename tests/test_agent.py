"""UsageAgent tool-use loop tests (offline) — fake Claude client + stub tools."""

import types

import pandas as pd

from usage_agent.agent import UsageAgent


def _block(**kwargs):
    return types.SimpleNamespace(**kwargs)


def _text_block(text):
    return _block(type="text", text=text)


def _tool_block(name, tool_input, id="t1"):
    return _block(type="tool_use", name=name, input=tool_input, id=id)


def _resp(stop_reason, content):
    return types.SimpleNamespace(stop_reason=stop_reason, content=content)


class FakeClient:
    """messages.create returns queued responses in order; records call kwargs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _agent(client, tool_functions=None):
    return UsageAgent(
        client=client,
        tool_functions=tool_functions or {},
        tool_specs=[],
        system_prompt="sys",
    )


def test_direct_answer_no_tools():
    client = FakeClient([_resp("end_turn", [_text_block("Engineering spent the most.")])])
    result = _agent(client).run("who spent the most?")
    assert result.answer == "Engineering spent the most."
    assert len(client.calls) == 1


def test_tool_use_then_answer():
    tools = {"run_dax_query": lambda dax: pd.DataFrame([{"Dept": "Eng", "Cost": 100}])}
    client = FakeClient(
        [
            _resp("tool_use", [_tool_block("run_dax_query", {"dax": "EVALUATE X"})]),
            _resp("end_turn", [_text_block("Engineering spent $100.")]),
        ]
    )
    result = _agent(client, tools).run("who spent the most?")
    assert result.answer == "Engineering spent $100."
    # The tool result (CSV) was appended as a user turn before the 2nd call.
    second_call_messages = client.calls[1]["messages"]
    tool_result = second_call_messages[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "Eng" in tool_result["content"] and "100" in tool_result["content"]


def test_unknown_tool_reports_error():
    agent = _agent(FakeClient([]))
    assert "unknown tool" in agent._dispatch("nope", {}).lower()


def test_tool_exception_is_reported_not_raised():
    def boom(**kwargs):
        raise RuntimeError("kaboom")

    agent = _agent(FakeClient([]), {"run_dax_query": boom})
    out = agent._dispatch("run_dax_query", {"dax": "X"})
    assert out.startswith("Error running 'run_dax_query'")
    assert "kaboom" in out


def test_serialise_variants():
    assert UsageAgent._serialise("hello") == "hello"
    assert UsageAgent._serialise(pd.DataFrame()) == "(no rows)"
    assert "a" in UsageAgent._serialise({"a": 1})
    csv = UsageAgent._serialise(pd.DataFrame([{"x": 1, "y": 2}]))
    assert "x,y" in csv and "1,2" in csv


def test_max_steps_guard():
    # Always asks for a tool -> loop should stop at max_steps.
    tools = {"run_dax_query": lambda dax: pd.DataFrame([{"n": 1}])}
    responses = [
        _resp("tool_use", [_tool_block("run_dax_query", {"dax": "X"})]) for _ in range(3)
    ]
    client = FakeClient(responses)
    result = UsageAgent(
        max_steps=3, client=client, tool_functions=tools, tool_specs=[], system_prompt="s"
    ).run("loop forever")
    assert "maximum number of tool-calling steps" in result.answer
