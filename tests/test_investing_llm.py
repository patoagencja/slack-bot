"""Structured-output LLM tests — schema validation, one repair retry, LLM_SCHEMA_ERROR."""

import pytest

from investing import llm
from investing.schemas import LLMQualitative, LLMSchemaError


class _Block:
    def __init__(self, name, inp):
        self.type = "tool_use"
        self.name = name
        self.input = inp


class _Resp:
    def __init__(self, name, inp):
        self.content = [_Block(name, inp)]


class _Messages:
    def __init__(self, queue):
        self._queue = queue
        self.calls = 0

    def create(self, **kwargs):
        inp = self._queue[min(self.calls, len(self._queue) - 1)]
        self.calls += 1
        tool_name = kwargs["tool_choice"]["name"]
        return _Resp(tool_name, inp)


class _FakeClient:
    def __init__(self, queue):
        self.messages = _Messages(queue)


def test_valid_response_parses():
    client = _FakeClient([{"thesis_summary": "ok", "bull_case": ["a"], "bear_case": ["b"]}])
    out = llm.extract_qualitative(system="s", user="u", client=client)
    assert isinstance(out, LLMQualitative)
    assert out.bull_case == ["a"]
    assert client.messages.calls == 1


def test_repair_retry_recovers():
    # first response invalid (thesis_summary must be str), second valid
    client = _FakeClient([
        {"thesis_summary": {"bad": "type"}},
        {"thesis_summary": "fixed", "bull_case": ["a"]},
    ])
    out = llm.extract_qualitative(system="s", user="u", client=client)
    assert out.thesis_summary == "fixed"
    assert client.messages.calls == 2  # initial + one repair


def test_persistent_failure_raises_schema_error():
    client = _FakeClient([{"thesis_summary": {"still": "bad"}}])
    with pytest.raises(LLMSchemaError):
        llm.extract_qualitative(system="s", user="u", client=client)


def test_tool_schema_generated_from_pydantic():
    tool = llm._tool_for(LLMQualitative, "submit_qualitative")
    assert tool["name"] == "submit_qualitative"
    assert "properties" in tool["input_schema"]
    assert "bull_case" in tool["input_schema"]["properties"]
