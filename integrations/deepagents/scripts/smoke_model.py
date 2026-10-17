"""Minimal real-provider tool-call check. Prints no prompts, responses or secrets."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


@tool
def add_two_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def load_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, help="Existing local env file; never copied")
    args = parser.parse_args()
    values = {**(load_env_file(args.env_file) if args.env_file else {}), **os.environ}
    model_name = values.get("DEEPAGENTS_MODEL") or values.get("BOOKDISTILL_LLM_MODEL")
    api_key = values.get("DEEPAGENTS_MODEL_API_KEY") or values.get("BOOKDISTILL_LLM_API_KEY")
    base_url = values.get("DEEPAGENTS_MODEL_BASE_URL") or values.get("BOOKDISTILL_LLM_BASE_URL")
    if not model_name or not api_key:
        print("model credentials unavailable")
        return 2
    try:
        model = ChatOpenAI(model=model_name, api_key=api_key, base_url=base_url or None,
                           temperature=0, timeout=20, max_retries=0)
        bound = model.bind_tools([add_two_numbers])
        first = bound.invoke([HumanMessage(content="Call add_two_numbers with a=2 and b=3.")])
        if not first.tool_calls or first.tool_calls[0]["name"] != "add_two_numbers":
            print("tool call: missing")
            return 1
        call = first.tool_calls[0]
        answer = add_two_numbers.invoke(call["args"])
        second = bound.invoke([HumanMessage(content="Call add_two_numbers with a=2 and b=3."),
                               first, ToolMessage(content=str(answer), tool_call_id=call["id"])])
        print(f"tool call: yes; second turn: {'yes' if second.content else 'empty'}; model: {model_name}")
        return 0 if second.content else 1
    except Exception as exc:
        print(f"tool call failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
