"""CLI prototype for one scoped research run."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .client import BookDistillClient, BookDistillError
from .workflow import ResearchRun


def main() -> int:
    parser = argparse.ArgumentParser(description="BookDistill Deep Agents research prototype")
    parser.add_argument("question", help="Research question")
    parser.add_argument("--book-id", action="append", default=[], help="Allowed book UUID; repeat for multiple books")
    parser.add_argument("--base-url", default=os.getenv("BOOKDISTILL_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--demo", action="store_true", help="Synthetic no-key model and data")
    args = parser.parse_args()
    try:
        if args.demo:
            from .demo import DEMO_BOOK_ID, DemoClient, ScriptedToolModel
            run = ResearchRun(DemoClient(), [DEMO_BOOK_ID])
            result = run.invoke(args.question, ScriptedToolModel(question=args.question))
        else:
            from langchain_openai import ChatOpenAI
            if not args.book_id:
                parser.error("--book-id is required outside demo mode")
            model_name = os.getenv("DEEPAGENTS_MODEL")
            model_key = os.getenv("DEEPAGENTS_MODEL_API_KEY")
            if not model_name or not model_key:
                parser.error("DEEPAGENTS_MODEL and DEEPAGENTS_MODEL_API_KEY are required")
            model = ChatOpenAI(model=model_name, api_key=model_key,
                               base_url=os.getenv("DEEPAGENTS_MODEL_BASE_URL") or None,
                               temperature=0)
            with BookDistillClient(args.base_url, os.getenv("BOOKDISTILL_API_KEY")) as client:
                result = ResearchRun(client, args.book_id).invoke(args.question, model)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BookDistillError, ValueError) as exc:
        print(f"Agent run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
