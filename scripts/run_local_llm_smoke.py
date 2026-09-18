"""Run one evidence-grounded answer through the local Transformers provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts.generation import (  # noqa: E402
    GenerationConfig,
    RagAnswerPayload,
    RagAnswerRequest,
    TraceContext,
)
from bookdistill.contracts.retrieval import Evidence  # noqa: E402
from bookdistill.generation import (  # noqa: E402
    LLMGateway,
    RagAnswerService,
    TransformersLocalProvider,
    default_prompt_registry,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("E:/mymodel/models/Qwen1.5-1.8B"),
    )
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    options = arguments()
    evidence_id = UUID("11111111-1111-4111-8111-111111111111")
    provider = TransformersLocalProvider(
        model_path=options.model,
        adapter_path=options.adapter,
        device=options.device,
        max_input_tokens=2048,
    )
    service = RagAnswerService(LLMGateway(provider, default_prompt_registry()))
    request = RagAnswerRequest(
        trace_context=TraceContext(request_id=uuid4(), trace_id=uuid4()),
        payload=RagAnswerPayload(
            question="RAG 为什么能够减少没有依据的回答？",
            evidence=[
                Evidence(
                    evidence_id=evidence_id,
                    chunk_id=UUID("22222222-2222-4222-8222-222222222222"),
                    book_id=UUID("33333333-3333-4333-8333-333333333333"),
                    book_title="检索增强生成入门",
                    chapter_id=UUID("44444444-4444-4444-8444-444444444444"),
                    chapter_path=["第 1 章", "RAG 基础"],
                    page_start=1,
                    page_end=1,
                    text=(
                        "检索增强生成先从受控资料中检索相关证据，再要求模型仅依据"
                        "这些证据回答并标注来源，因此能够减少没有依据的回答。"
                    ),
                    fusion_score=1.0,
                    fusion_rank=1,
                    final_rank=1,
                    retrieval_version="local-smoke.v1",
                )
            ],
        ),
        generation_config=GenerationConfig(
            temperature=options.temperature,
            max_output_tokens=options.max_output_tokens,
        ),
    )
    response = service.answer(request)
    print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
