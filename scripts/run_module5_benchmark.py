"""Frozen real-PDF evaluation: parse/chunk -> retrieval -> gated generation.

Calls existing module interfaces only; gold labels never enter the generator.
All model errors/degradations are artifacts, not silently substituted baselines.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
import unicodedata
import re
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bookdistill.contracts import (DocumentProcessingSnapshot, EvaluationDataset, ExperimentConfig,
    RetrievalConfig, RetrievalRequest, RetrievalScope, RetrievalPredictionSet, RetrievalPrediction, RetrievedItem)
from bookdistill.contracts.generation import RagAnswerRequest, RagAnswerPayload, GenerationConfig, TraceContext
from bookdistill.evaluation import evaluate_retrieval, write_experiment_artifacts
from bookdistill.evaluation.annotation import assert_dataset_references
from bookdistill.evaluation.release import digest, eligibility, generation_gate, retrieval_checks
from bookdistill.generation.factory import build_generation_service
from bookdistill.retrieval import (HybridRetrievalEngine, HashingEmbeddingProvider, FastEmbedEmbeddingProvider,
    TokenOverlapReranker, FastEmbedReranker, EmbeddingSimilarityReranker)
from bookdistill.settings import Settings


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normal(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def matrix():
    specs = []
    for embedding in ["hashing", "fastembed"]:
        for variant in ["sparse", "dense", "hybrid", "hybrid_rerank"]:
            specs.append(dict(id=f"{embedding}-{variant}", embedding=embedding, variant=variant,
                              reranker="token_overlap" if variant == "hybrid_rerank" else "none"))
        specs.append(dict(id=f"{embedding}-semantic-rerank", embedding=embedding,
                          variant="hybrid_rerank", reranker="embedding_similarity"))
    specs.append(dict(id="fastembed-cross-encoder", embedding="fastembed", variant="hybrid_rerank", reranker="cross_encoder"))
    return specs


def parse_checks(snapshot, annotations):
    pages = {p.page_index: p for p in snapshot.document.pages}
    chunks = {str(c.chunk_id): c for c in snapshot.chunks}
    blocks = {str(b.block_id): b for p in pages.values() for b in p.blocks}
    checks = []
    for row in annotations["cases"]:
        errors = []
        for point in row["required_evidence_groups"]:
            page = pages.get(point["page"])
            if not page or normal(point["quote"]) not in normal(" ".join(b.text for b in page.blocks)):
                errors.append("parse_anchor_missing")
            candidates = [chunks[cid] for cid in point["chunk_ids"] if cid in chunks]
            if not any(normal(point["quote"]) in normal(c.text + " ".join(c.chapter_path)) for c in candidates):
                errors.append("chunk_anchor_missing")
            if not any(c.page_start <= point["page"] <= c.page_end for c in candidates):
                errors.append("page_mapping_failure")
        checks.append(dict(case_id=row["case_id"], failures=sorted(set(errors))))
    sizes = sorted(c.token_count for c in snapshot.chunks)
    invalid_sources = sum(any(str(b) not in blocks or not c.page_start <= blocks[str(b)].page_index <= c.page_end
                              for b in c.source_block_ids) for c in snapshot.chunks)
    return dict(cases=checks, source_location_invalid_chunks=invalid_sources,
                chunk_count=len(sizes), short_chunk_ratio=sum(s < 64 for s in sizes)/len(sizes),
                oversized_chunk_ratio=sum(s > 512 for s in sizes)/len(sizes),
                tokens={"min": min(sizes), "p50": sizes[len(sizes)//2], "p95": sizes[int(len(sizes)*.95)], "max": max(sizes)},
                sampled_anchor_cases=sum(bool(r["required_evidence_groups"]) for r in annotations["cases"]),
                human_semantic_completeness=None, reading_order_accuracy=None, title_f1=None,
                note="Source/anchor checks are mechanical. OCR, full reading order and semantic integrity require independent human ground truth.")


def classify(case, annotation, response, upstream):
    if upstream:
        return upstream, False
    trace = response.trace
    if trace.dense_degraded or trace.sparse_degraded or trace.rerank_degraded:
        return ["retrieval_stage_error_or_timeout"], False
    if not case.is_answerable:
        return [], True  # keep actual retrieved distractors; never force empty context
    evidence = {str(e.chunk_id) for e in response.evidence}
    groups = annotation["required_evidence_groups"]
    covered = all(evidence.intersection(p["chunk_ids"]) for p in groups)
    if covered:
        return [], True
    candidates = {str(c.chunk_id) for c in trace.candidates}
    if any(not candidates.intersection(p["chunk_ids"]) for p in groups):
        return ["candidate_recall_or_fusion_truncation"], False
    return ["rerank_or_final_selection_lost_required_evidence"], False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, default=ROOT / "artifacts/module5-real-pdf-v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true", help="Create a new immutable run lock; reruns verify it")
    parser.add_argument("--generator", choices=["none", "demo", "configured"], default="demo")
    parser.add_argument("--generation-variant", default="fastembed-hybrid")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/model-cache")
    args = parser.parse_args()
    batch = args.batch
    snapshot_path = batch / "corpus/snapshot.v1.json"
    dataset_path = batch / "annotations/dataset.json"
    annotations_path = batch / "annotations/annotations.json"
    snapshot = DocumentProcessingSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    dataset_raw, annotations = read(dataset_path), read(annotations_path)
    dataset = EvaluationDataset.model_validate(dataset_raw)
    assert_dataset_references(dataset, [snapshot])
    if annotations["snapshot_sha256"] != sha(snapshot_path) or annotations["dataset_sha256"] != sha(dataset_path):
        raise ValueError("Annotation snapshot/dataset fingerprint mismatch")
    pdf = ROOT / snapshot.book.original_file_name
    if sha(pdf) != snapshot.book.sha256 or annotations["pdf_sha256"] != snapshot.book.sha256:
        raise ValueError("PDF changed since annotation")
    thresholds = read(ROOT / "examples/retrieval_release_thresholds.v1.json")
    config = RetrievalConfig(dense_top_k=30, sparse_top_k=30, fusion_candidates=40, rerank_candidates=30,
                             final_evidence=10, max_per_chapter=10, context_window=0, context_token_budget=8000,
                             max_context_tokens_per_evidence=512)
    gen_config = GenerationConfig(temperature=0, max_output_tokens=1200)
    settings = Settings.from_environment() if args.generator == "configured" else Settings(
        database_url="sqlite://", storage_root=args.output, llm_provider="demo" if args.generator == "demo" else "disabled")
    packages = {}
    for name in ["pymupdf", "pydantic", "fastembed", "onnxruntime", "numpy", "torch", "transformers", "peft"]:
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    models = {p.relative_to(args.cache_dir).as_posix(): sha(p) for p in sorted(args.cache_dir.rglob("*"))
              if p.is_file() and p.suffix in {".onnx", ".json", ".txt"} and "snapshots" in p.parts}
    local_model_files = {}
    for configured_path in (settings.llm_local_model_path, settings.llm_local_adapter_path):
        if configured_path and configured_path.is_dir():
            local_model_files[str(configured_path.resolve())] = {
                str(p.relative_to(configured_path)): sha(p)
                for p in sorted(configured_path.rglob("*")) if p.is_file()
            }
    source = {p.relative_to(ROOT).as_posix(): sha(p) for base in [ROOT / "src", ROOT / "scripts"] for p in sorted(base.rglob("*.py"))}
    lock = dict(schema_version="1.0", pdf_sha256=sha(pdf), snapshot_sha256=sha(snapshot_path), dataset_sha256=sha(dataset_path),
                annotations_sha256=sha(annotations_path), source_sha256=digest(source), packages=packages,
                python=platform.python_version(), platform=platform.platform(), cpu=platform.processor(),
                models=models, matrix=matrix(), retrieval=config.model_dump(mode="json"), thresholds=thresholds,
                generator=dict(mode=args.generator, provider=settings.llm_provider, model=settings.llm_model,
                               base_url=settings.llm_base_url if args.generator == "configured" else None,
                               local_model=str(settings.llm_local_model_path), local_adapter=str(settings.llm_local_adapter_path),
                               local_model_file_sha256={k: digest(v) for k,v in local_model_files.items()},
                               prompt_version="rag-answer.v1", configuration=gen_config.model_dump()),
                generation_variant=args.generation_variant, threads=2)
    lock_path = batch / "benchmark.lock.json"
    if args.freeze:
        if lock_path.exists():
            raise ValueError("Lock exists; do not overwrite a frozen run. Use a versioned batch.")
        write(lock_path, lock)
        write(batch / "source-fingerprints.json", source)
    elif not lock_path.exists() or read(lock_path) != lock:
        raise ValueError("Frozen configuration changed or lock absent. Freeze a new version before running.")
    if args.output.exists():
        raise ValueError("Output exists; choose a new run directory to preserve previous results")
    args.output.mkdir(parents=True)
    write(args.output / "run-lock.json", lock)
    upstream = parse_checks(snapshot, annotations)
    write(args.output / "parse-chunk.json", upstream)
    upstream_by_id = {r["case_id"]: r["failures"] for r in upstream["cases"]}
    annotation_by_id = {r["case_id"]: r for r in annotations["cases"]}
    blockers = eligibility(dataset_raw, annotations)
    acceptance = dict(eligible=not blockers, classification="quality_acceptance" if not blockers else "mechanism_test",
                      blockers=blockers, dataset_sha256=digest(dataset_raw), generator_kind=settings.llm_provider)
    os.environ["HF_HUB_OFFLINE"] = "1"
    embeddings = {"hashing": HashingEmbeddingProvider()}
    results, evaluated_by_id, generation_rows = [], {}, []
    engines = {}
    for spec in matrix():
        print(f"START {spec['id']}", flush=True)
        try:
            name = spec["embedding"]
            if name not in embeddings:
                if not any("bge-small-zh" in path and path.endswith(".onnx") for path in models):
                    raise RuntimeError("Dense model artifact unavailable offline")
                embeddings[name] = FastEmbedEmbeddingProvider(cache_dir=args.cache_dir, threads=2)
            embedding = embeddings[name]
            if spec["reranker"] == "cross_encoder":
                if not any("bge-reranker" in path and path.endswith(".onnx") for path in models):
                    raise RuntimeError("CrossEncoder weights unavailable offline; no fallback permitted")
                reranker = FastEmbedReranker(cache_dir=args.cache_dir, threads=2)
            elif spec["reranker"] == "embedding_similarity":
                reranker = EmbeddingSimilarityReranker(embedding)
            else:
                reranker = TokenOverlapReranker()
            if name not in engines:
                engine = HybridRetrievalEngine(embedding=embedding, reranker=reranker)
                t = perf_counter()
                engine.index_chunks(snapshot.chunks, {snapshot.book.book_id: snapshot.book.title})
                engines[name] = (engine, (perf_counter()-t)*1000)
            engine, index_ms = engines[name]
            engine.reranker = reranker
            predictions, diagnostics, responses = [], [], {}
            for case in dataset.cases:
                t = perf_counter()
                try:
                    request = RetrievalRequest(request_id=uuid4(), query=case.question,
                        scope=RetrievalScope(book_ids=case.book_scope, chapter_ids=case.chapter_scope),
                        config=config.model_copy(update={"variant": spec["variant"]}))
                    response = engine.search(request)
                    failures, sufficient = classify(case, annotation_by_id[case.case_id], response, upstream_by_id[case.case_id])
                    degraded = any("error_or_timeout" in s for s in failures)
                    prediction = RetrievalPrediction(case_id=case.case_id, latency_ms=response.trace.total_latency_ms,
                        retrieved=[] if degraded else [RetrievedItem(chunk_id=e.chunk_id, score=e.rerank_score or e.fusion_score) for e in response.evidence],
                        error="stage_degraded" if degraded else None)
                    responses[case.case_id] = response
                    diagnostics.append(dict(case_id=case.case_id, failures=failures, evidence_sufficient=sufficient,
                                            trace=response.trace.model_dump(mode="json"), evidence=response.model_dump(mode="json")["evidence"]))
                except Exception as error:
                    prediction = RetrievalPrediction(case_id=case.case_id, latency_ms=(perf_counter()-t)*1000, error=type(error).__name__)
                    diagnostics.append(dict(case_id=case.case_id, failures=["retrieval_error:"+type(error).__name__], evidence_sufficient=False))
                predictions.append(prediction)
            prediction_set = RetrievalPredictionSet(dataset_name=dataset.dataset_name, dataset_version=dataset.dataset_version, predictions=predictions)
            experiment = ExperimentConfig(experiment_id=spec["id"], dataset_version=dataset.dataset_version,
                retrieval_variant=spec["variant"], parser_version=snapshot.document.parser_version, chunk_version=snapshot.chunks[0].chunk_version,
                embedding_model=embedding.model_name, reranker_model=getattr(reranker, "model_name", type(reranker).__name__),
                code_version=lock["source_sha256"], sparse_config={"method":"BM25"}, fusion_config=config.model_dump(mode="json"),
                notes="Frozen real PDF; AI annotation candidates; independent human review pending.")
            evaluated = evaluate_retrieval(dataset, prediction_set, experiment)
            evaluated_by_id[spec["id"]] = evaluated
            out = args.output / spec["id"]
            write_experiment_artifacts(evaluated, out)
            write(out / "predictions.json", prediction_set.model_dump(mode="json"))
            write(out / "diagnostics.json", diagnostics)
            for split in ["dev", "test"]:
                selected = [c for c in dataset.cases if annotation_by_id[c.case_id]["split"] == split]
                sub = dataset.model_copy(update={"split":split, "cases":selected})
                pred = prediction_set.model_copy(update={"predictions":[p for p in predictions if p.case_id in {c.case_id for c in selected}]})
                write_experiment_artifacts(evaluate_retrieval(sub, pred, experiment), out / split)
            agg = evaluated.aggregate
            metrics = dict(recall_at_10=next(c.recall for c in agg.cutoffs if c.k==10), mrr=agg.mrr, ndcg_at_10=agg.ndcg_at_10,
                           latency_p95_ms=agg.latency_p95_ms, missing_or_failed_predictions=sum(p.error is not None for p in predictions))
            checks = retrieval_checks(metrics, thresholds)
            result = dict(**spec, metrics=metrics, checks=checks, metrics_passed=all(checks.values()),
                          evidence_sufficient_cases=sum(d["evidence_sufficient"] for d,c in zip(diagnostics,dataset.cases) if c.is_answerable),
                          index_build_ms=index_ms, status="completed", acceptance=acceptance)
            write(out / "release-input.json", {"acceptance":acceptance, "variants":[{"retrieval_variant":"hybrid_rerank", "metrics":metrics}],
                                                "actual_variant":spec, "note":"Target gate thresholds applied uniformly for comparison; actual variant is recorded."})
            results.append(result)
            if spec["id"] == args.generation_variant:
                service = build_generation_service(settings)
                for case, diagnosis in zip(dataset.cases, diagnostics):
                    row = dict(case_id=case.case_id, status="blocked_retrieval", error=None, failures=list(diagnosis["failures"]))
                    if diagnosis["evidence_sufficient"] and service is not None:
                        try:
                            req = RagAnswerRequest(trace_context=TraceContext(request_id=uuid4(), trace_id=uuid4()),
                                payload=RagAnswerPayload(question=case.question, evidence=responses[case.case_id].evidence,
                                    scope=RetrievalScope(book_ids=case.book_scope, chapter_ids=case.chapter_scope),
                                    language="en" if case.question.isascii() else "zh-CN"), generation_config=gen_config)
                            answer = service.answer(req).model_dump(mode="json")
                            row.update(answer, status="ok")
                            if case.is_answerable and answer["insufficient_evidence"]:
                                row["failures"].append("refusal_with_recalled_evidence")
                            if not case.is_answerable and (not answer["insufficient_evidence"] or answer["citations"]):
                                row["failures"].append("unanswerable_not_refused_without_citations")
                        except Exception as error:
                            row.update(status="generation_error", error=type(error).__name__)
                            row["failures"].append("generation_error:"+type(error).__name__)
                    elif service is None:
                        row["status"] = "generation_disabled"
                    row.update(reviewers=[], citation_reviews=[], claim_faithfulness_scores=[], required_point_coverage=[],
                               answer_assertions_exhaustively_reviewed=False, reviewed_response_sha256=None)
                    generation_rows.append(row)
            print(f"DONE {spec['id']} Recall@10={metrics['recall_at_10']:.4f}", flush=True)
        except Exception as error:
            results.append(dict(**spec, status="blocked", error=type(error).__name__ + ": " + str(error),
                                affected_case_ids=[c.case_id for c in dataset.cases]))
            print(f"BLOCKED {spec['id']}: {type(error).__name__}", flush=True)
    if not generation_rows:
        generation_rows = [dict(case_id=c.case_id,status="generation_variant_unavailable",error="generation_variant_unavailable") for c in dataset.cases]
    generation_payload = {"acceptance": acceptance, "results":generation_rows, "lock_sha256":digest(lock)}
    write(args.output / "generation-results.json", generation_payload)
    gate = generation_gate(dataset_raw, generation_payload)
    write(args.output / "generation-gate.json", gate)
    for split in ["dev", "test"]:
        split_dataset = read(batch / "annotations" / f"dataset.{split}.json")
        selected_ids = {c["case_id"] for c in split_dataset["cases"]}
        split_payload = {**generation_payload, "results":[r for r in generation_rows if r["case_id"] in selected_ids]}
        write(args.output / f"generation-results.{split}.json", split_payload)
        write(args.output / f"generation-gate.{split}.json", generation_gate(split_dataset, split_payload))
    with (args.output / "human-review-template.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        import csv
        fields = ["case_id", "split", "question", "should_answer", "reference_answer", "required_points",
                  "system_answer", "claims", "citations", "citation_reviews_json", "claim_scores_0_or_1_json",
                  "required_point_coverage_json", "reviewer_1_id", "reviewer_1_reviewed_at",
                  "reviewer_1_page_correct_json", "reviewer_1_claim_scores_json", "reviewer_1_points_covered_json",
                  "reviewer_1_refusal_correct", "reviewer_1_assertions_exhaustive", "reviewer_1_agrees_with_scores",
                  "reviewer_2_id", "reviewer_2_reviewed_at", "reviewer_2_page_correct_json",
                  "reviewer_2_claim_scores_json", "reviewer_2_points_covered_json", "reviewer_2_refusal_correct",
                  "reviewer_2_assertions_exhaustive", "reviewer_2_agrees_with_scores",
                  "adjudicated_page_correct_json", "adjudicated_claim_scores_json",
                  "adjudicated_points_covered_json", "adjudication_notes", "failure_reason"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        case_by_id = {c.case_id:c for c in dataset.cases}
        for row in generation_rows:
            case = case_by_id[row["case_id"]]
            writer.writerow({"case_id":case.case_id,"split":annotation_by_id[case.case_id]["split"],
                "question":case.question,"should_answer":case.is_answerable,"reference_answer":case.reference_answer or "",
                "required_points":json.dumps(case.required_points,ensure_ascii=False),"system_answer":row.get("answer", ""),
                "claims":json.dumps(row.get("claims",[]),ensure_ascii=False),"citations":json.dumps(row.get("citations",[]),ensure_ascii=False),
                "citation_reviews_json":"","claim_scores_0_or_1_json":"","required_point_coverage_json":"",
                "reviewer_1_id":"","reviewer_1_reviewed_at":"","reviewer_1_agrees_with_scores":"",
                "reviewer_1_page_correct_json":"","reviewer_1_claim_scores_json":"","reviewer_1_points_covered_json":"",
                "reviewer_1_refusal_correct":"","reviewer_1_assertions_exhaustive":"",
                "reviewer_2_id":"","reviewer_2_reviewed_at":"","reviewer_2_agrees_with_scores":"",
                "reviewer_2_page_correct_json":"","reviewer_2_claim_scores_json":"","reviewer_2_points_covered_json":"",
                "reviewer_2_refusal_correct":"","reviewer_2_assertions_exhaustive":"",
                "adjudicated_page_correct_json":"","adjudicated_claim_scores_json":"",
                "adjudicated_points_covered_json":"",
                "adjudication_notes":"","failure_reason":";".join(row.get("failures",[])) or row.get("error", "")})
    primary = [evaluated_by_id.get(f"fastembed-{v}") for v in ["sparse", "dense", "hybrid", "hybrid_rerank"]]
    if all(primary):
        from bookdistill.evaluation import build_ablation_report
        ablation = build_ablation_report(primary)
        ablation_data = ablation.model_dump(mode="json")
        ablation_data["acceptance"] = {"eligible": not blockers, "classification": acceptance["classification"],
                                       "blockers": blockers, "dataset_sha256": digest(dataset_raw)}
        write(args.output / "ablation.json", ablation_data)
    else:
        write(args.output / "ablation.json", {"schema_version":"1.0", "variants":[], "acceptance": {
            "eligible":False, "classification":"mechanism_test", "blockers":blockers + ["fastembed_core_variants_incomplete"]}})
    summary = dict(classification=acceptance["classification"], acceptance=acceptance, variants=results,
                   generation_gate=gate, lock_sha256=digest(lock), created_at=datetime.now(timezone.utc).isoformat())
    write(args.output / "summary.json", summary)
    lines = ["# 模块五真实 PDF 评测", "", "结论：机制测试；不构成质量验收。", "",
             f"PDF：{snapshot.book.original_file_name}；{len(snapshot.document.pages)}页；{len(snapshot.chunks)} Chunks。",
             f"题数：{len(dataset.cases)}；可回答{sum(c.is_answerable for c in dataset.cases)}；待双人复核。",
             f"配置锁：`{digest(lock)}`。生成模式：`{settings.llm_provider}`。", "",
             "| 配置 | Recall@10 | MRR | nDCG@10 | P95 ms | 状态 |", "|---|---:|---:|---:|---:|---|"]
    for r in results:
        m=r.get("metrics",{})
        lines.append(f"| {r['id']} | {m.get('recall_at_10','—')} | {m.get('mrr','—')} | {m.get('ndcg_at_10','—')} | {m.get('latency_p95_ms','—')} | {r['status']}: {r.get('error','')} |")
    lines += ["", "生成忠实度与语义引用正确性：未完成独立人工评分，不能据此给出质量结论。",
              "生成仅对必要证据组全部召回的可回答题执行；不可回答题保留实际检索到的干扰证据。",
              "缺失和跳过案例保留在门禁中；自动零分是未完成验收的保守处理，不是人工质量分。",
              "", "## 逐题问题（全量）", ""]
    for row in generation_rows:
        lines.append(f"- {row['case_id']}: {row['status']}; {', '.join(row.get('failures',[])) or 'awaiting_human_review'}")
    lines += ["", "检索逐题证据与阶段排名见各配置 diagnostics.json；完整逐题分数见 cases.csv。",
              "缺失 CrossEncoder 权重会报告 blocked，不替换成其他 reranker。",
              "同组改写不跨 dev/test；当前 test 为候选集，本轮查看后不能再宣称是未见测试集。"]
    (args.output / "report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(args.output / "report.md")


if __name__ == "__main__":
    main()
