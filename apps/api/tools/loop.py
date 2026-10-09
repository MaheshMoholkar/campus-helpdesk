"""The hand-written tool loop (docs/spec.md section 10.7).

Ask the model; if it wants a read tool, run it and hand back the result; repeat
until it answers in text or the round limit is hit. An action tool is never run
here: it becomes a pending action that the user must confirm.
"""

import json
from dataclasses import dataclass, field

from apps.api import prompts
from apps.api.erp import ErpClient, ErpCredentials
from apps.api.llm.base import LLMClient, Message, Usage
from apps.api.tools.erp_tools import ACTION_TOOLS, run_read_tool, tools_for

MAX_ROUNDS = 3


@dataclass
class ProposedAction:
    tool_name: str
    arguments: dict


@dataclass
class ToolLoopResult:
    text: str
    tools_called: list[str] = field(default_factory=list)
    proposed_action: ProposedAction | None = None


def run_tool_loop(
    llm: LLMClient,
    erp: ErpClient,
    creds: ErpCredentials,
    person_kind: str | None,
    history: list[dict],
    question: str,
    usage: Usage,
) -> ToolLoopResult:
    messages: list[Message] = [{"role": "system", "content": prompts.TOOLS_SYSTEM}]
    for turn in history[-4:]:
        messages.append(
            {"role": "user" if turn["speaker"] == "user" else "assistant", "content": turn["text"]}
        )
    messages.append({"role": "user", "content": question})

    definitions, read_tools = tools_for(person_kind)
    offered = {d["function"]["name"] for d in definitions}
    called: list[str] = []
    for _ in range(MAX_ROUNDS):
        result = llm.chat(messages, tools=definitions, max_tokens=400)
        usage.add(result.usage)
        if not result.tool_calls:
            return ToolLoopResult(result.text.strip(), called)

        # An action ends the loop at once: nothing is executed until the user confirms.
        for call in result.tool_calls:
            if call.name in ACTION_TOOLS and call.name in offered:
                purpose = str(call.arguments.get("purpose") or "general purpose")[:200]
                called.append(call.name)
                return ToolLoopResult("", called, ProposedAction(call.name, {"purpose": purpose}))

        messages.append(
            {
                "role": "assistant",
                "content": result.text or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in result.tool_calls
                ],
            }
        )
        for call in result.tool_calls:
            if call.name in read_tools:
                output = run_read_tool(erp, creds, call.name, call.arguments)
                called.append(call.name)
            else:
                output = {"error": f"unknown tool {call.name}"}
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(output, default=str)}
            )

    # Out of rounds: ask for a final answer without offering tools.
    result = llm.chat(messages, max_tokens=400)
    usage.add(result.usage)
    return ToolLoopResult(result.text.strip(), called)
