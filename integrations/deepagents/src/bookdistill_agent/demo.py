"""Synthetic, no-key workflow exercise; no claims about real books or models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


DEMO_BOOK_ID = "00000000-0000-4000-8000-000000000001"
DEMO_EVIDENCE_ID = "00000000-0000-4000-8000-000000000002"


class DemoClient:
    def search(self, query: str, book_ids: list[str], **_: Any) -> dict[str, Any]:
        assert book_ids == [DEMO_BOOK_ID]
        return {"schema_version": "1.0", "evidence": [{
            "schema_version": "1.0", "evidence_id": DEMO_EVIDENCE_ID,
            "chunk_id": "00000000-0000-4000-8000-000000000003",
            "book_id": DEMO_BOOK_ID, "book_title": "演示书籍（虚构）",
            "chapter_id": "00000000-0000-4000-8000-000000000004",
            "chapter_path": ["演示章"], "page_start": 1, "page_end": 1,
            "text": "这是一段合成证据，仅用于验证工具调用链。",
            "fusion_score": 1.0, "fusion_rank": 1, "final_rank": 1,
            "retrieval_version": "demo",
        }], "trace": {}}

    def answer(self, question: str, book_ids: list[str], evidence: list[dict[str, Any]],
               **_: Any) -> dict[str, Any]:
        assert book_ids == [DEMO_BOOK_ID]
        assert [item["evidence_id"] for item in evidence] == [DEMO_EVIDENCE_ID]
        return {"schema_version": "1.0", "answer": "合成证据证明工具链已运行。",
                "claims": [{"text": "工具链已运行", "evidence_ids": [DEMO_EVIDENCE_ID]}],
                "citations": [{"evidence_id": DEMO_EVIDENCE_ID,
                               "chunk_id": evidence[0]["chunk_id"], "book_id": DEMO_BOOK_ID,
                               "book_title": evidence[0]["book_title"],
                               "chapter_id": evidence[0]["chapter_id"],
                               "chapter_path": ["演示章"], "page_start": 1, "page_end": 1,
                               "quote": evidence[0]["text"]}],
                "insufficient_evidence": False, "follow_up": None,
                "model_provider": "demo", "model_name": "scripted", "prompt_version": "demo",
                "usage": {}, "latency_ms": 0, "provider_request_id": "demo",
                "repair_attempted": False, "created_at": datetime.now(timezone.utc).isoformat()}


class ScriptedToolModel(BaseChatModel):
    """Exercise LangGraph's actual tool loop without external credentials."""

    question: str = "演示问题"

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedToolModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            response = AIMessage(content="", tool_calls=[{
                "name": "search_evidence", "args": {"query": self.question}, "id": "demo-search"}])
        elif not any(message.name == "answer_from_evidence" for message in tool_messages):
            evidence = json.loads(str(tool_messages[-1].content))
            response = AIMessage(content="", tool_calls=[{
                "name": "answer_from_evidence", "args": {"question": self.question,
                                                   "evidence_ids": [evidence[0]["evidence_id"]]},
                "id": "demo-answer"}])
        else:
            response = AIMessage(content="完成")
        return ChatResult(generations=[ChatGeneration(message=response)])
