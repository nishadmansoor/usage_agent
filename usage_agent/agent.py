"""The usage agent: a Claude tool-calling orchestrator over the Fabric data.

``UsageAgent.run`` drives a standard Anthropic Messages API tool-use loop:

1. Send the conversation + tool schemas to Claude.
2. While ``stop_reason == "tool_use"``, dispatch the requested tools to the
   registered Python callables, append the results as a user turn, and loop.
3. When Claude returns a normal turn, return its text.

Tool results that are pandas DataFrames are serialised to compact CSV before
being handed back to the model. Ported from sla_teams and simplified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .config import get_settings
from .llm import get_claude_client
from .logging_config import get_logger
from .prompts.system_prompt import get_agent_system_prompt
from .tools import get_tool_functions, get_tool_specs

logger = get_logger(__name__)


@dataclass
class AgentResult:
    """Outcome of a single agent run."""

    answer: str


class UsageAgent:
    """Conversational agent over the AI usage/cost tools, powered by Claude.

    The Claude client, tool set, and system prompt are injectable so the loop can
    be unit-tested offline without touching the network.
    """

    def __init__(
        self,
        max_steps: int = 14,
        *,
        client=None,
        tool_functions: dict[str, Any] | None = None,
        tool_specs: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        settings = get_settings()
        self._client = client or get_claude_client()
        self._model = settings.anthropic_model
        self._max_tokens = settings.claude_max_tokens
        self._max_steps = max_steps

        self._system_prompt = system_prompt or get_agent_system_prompt()
        self._tools = tool_specs if tool_specs is not None else get_tool_specs()
        self._tool_functions = (
            tool_functions if tool_functions is not None else get_tool_functions()
        )

    # -- public API --------------------------------------------------------
    def run(self, user_message: str) -> AgentResult:
        """Answer *user_message*, calling tools as needed."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        for step in range(self._max_steps):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system_prompt,
                tools=self._tools,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                logger.info(
                    "Agent finished in %d step(s) (stop_reason=%s).",
                    step + 1,
                    response.stop_reason,
                )
                return AgentResult(self._text_of(response))

            # Echo Claude's assistant turn (text + tool_use blocks) back verbatim.
            messages.append({"role": "assistant", "content": response.content})

            # Execute each requested tool and collect results for one user turn.
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = self._dispatch(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})

        return AgentResult("Stopped: reached the maximum number of tool-calling steps.")

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _text_of(response) -> str:
        """Concatenate the text blocks of a Claude response."""
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def _dispatch(self, name: str, tool_input: dict | None) -> str:
        """Run a single tool call and return its result as text."""
        func = self._tool_functions.get(name)
        if func is None:
            return f"Error: unknown tool '{name}'."

        args = dict(tool_input or {})
        logger.info("Tool call: %s(%s)", name, args)
        try:
            result = func(**args)
        except Exception as exc:  # noqa: BLE001 - report tool errors back to the model
            logger.exception("Tool '%s' failed", name)
            return f"Error running '{name}': {exc}"

        return self._serialise(result)

    @staticmethod
    def _serialise(result: Any) -> str:
        """Convert a tool result into compact text for the model."""
        if isinstance(result, pd.DataFrame):
            if result.empty:
                return "(no rows)"
            return result.to_csv(index=False)
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)
