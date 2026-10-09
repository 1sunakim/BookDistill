"""Export the machine-readable JSON Schema for other services."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    DocumentProcessingSnapshot,
    IndexChunksRequest,
    RagAnswerRequest,
    RagAnswerResponse,
    RetrieveAndAnswerRequest,
    RetrieveAndAnswerResponse,
    KnowledgeRequest,
    KnowledgeResponse,
    QuestionRequest,
    QuestionResponse,
    SummaryRequest,
    SummaryResponse,
    ViewpointRequest,
    ViewpointResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from bookdistill.contracts.image_assets import ImageAssetList, ImageAssetView  # noqa: E402


def main() -> None:
    schemas = {
        "images.asset-view.v1.schema.json": ImageAssetView,
        "images.list.v1.schema.json": ImageAssetList,
        "document-processing.snapshot.v1.schema.json": DocumentProcessingSnapshot,
        "retrieval.index-chunks-request.v1.schema.json": IndexChunksRequest,
        "retrieval.request.v1.schema.json": RetrievalRequest,
        "retrieval.response.v1.schema.json": RetrievalResponse,
        "generation.rag-answer-request.v1.schema.json": RagAnswerRequest,
        "generation.rag-answer-response.v1.schema.json": RagAnswerResponse,
        "generation.retrieve-and-answer-request.v1.schema.json": RetrieveAndAnswerRequest,
        "generation.retrieve-and-answer-response.v1.schema.json": RetrieveAndAnswerResponse,
        "generation.summary-request.v1.schema.json": SummaryRequest,
        "generation.summary-response.v1.schema.json": SummaryResponse,
        "generation.knowledge-request.v1.schema.json": KnowledgeRequest,
        "generation.knowledge-response.v1.schema.json": KnowledgeResponse,
        "generation.questions-request.v1.schema.json": QuestionRequest,
        "generation.questions-response.v1.schema.json": QuestionResponse,
        "generation.viewpoint-request.v1.schema.json": ViewpointRequest,
        "generation.viewpoint-response.v1.schema.json": ViewpointResponse,
    }
    for file_name, model in schemas.items():
        output_path = PROJECT_ROOT / "schemas" / file_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(output_path)


if __name__ == "__main__":
    main()
