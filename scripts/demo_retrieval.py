from __future__ import annotations

import json
from pathlib import Path
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts import (  # noqa: E402
    DocumentProcessingSnapshot,
    RetrievalConfig,
    RetrievalRequest,
)
from bookdistill.retrieval import HybridRetrievalEngine  # noqa: E402


snapshot = DocumentProcessingSnapshot.model_validate_json(
    (PROJECT_ROOT / "examples" / "document_processing_snapshot.v1.json").read_text(encoding="utf-8")
)
engine = HybridRetrievalEngine()
engine.index_chunks(snapshot.chunks, {snapshot.book.book_id: snapshot.book.title})
response = engine.search(
    RetrievalRequest(
        request_id=uuid4(),
        query="回答中的页码应该如何产生？",
        config=RetrievalConfig(
            dense_top_k=10,
            sparse_top_k=10,
            fusion_candidates=10,
            rerank_candidates=10,
            final_evidence=2,
        ),
    )
)
print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
