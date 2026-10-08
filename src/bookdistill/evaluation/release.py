"""Fail-closed release checks. Semantic judgements must come from human reviews."""
from __future__ import annotations
import hashlib
import json
import math


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def retrieval_checks(metrics, thresholds):
    rules = {"recall_at_10": ("minimum_recall_at_10", True), "mrr": ("minimum_mrr", True),
             "ndcg_at_10": ("minimum_ndcg_at_10", True), "latency_p95_ms": ("maximum_latency_p95_ms", False),
             "missing_or_failed_predictions": ("maximum_missing_or_failed_predictions", False)}
    return {name: isinstance(metrics.get(name), (int, float)) and math.isfinite(metrics[name])
            and (metrics[name] >= thresholds[key] if minimum else metrics[name] <= thresholds[key])
            for name, (key, minimum) in rules.items()}


def human_pair(reviewers, field):
    valid = [r for r in reviewers if r.get("kind") == "human" and r.get("reviewer_id")
             and r.get("reviewed_at") and r.get(field) is True]
    return len({r["reviewer_id"] for r in valid}) >= 2


def eligibility(dataset, annotations, *, minimum_cases=100, minimum_books=2):
    reasons = []
    cases = dataset["cases"]
    if dataset.get("split") != "test":
        reasons.append("not_a_frozen_test_split")
    if len(cases) < minimum_cases:
        reasons.append(f"sample_size_below_{minimum_cases}")
    if len({b for c in cases for b in c["book_scope"]}) < minimum_books:
        reasons.append(f"book_count_below_{minimum_books}")
    if not any(not c["is_answerable"] for c in cases):
        reasons.append("no_unanswerable_cases")
    by_id = {r["case_id"]: r for r in annotations.get("cases", [])}
    for c in cases:
        if c.get("review_status") != "approved" or not human_pair(by_id.get(c["case_id"], {}).get("reviewers", []), "approved"):
            reasons.append(f"{c['case_id']}:missing_two_human_annotation_approvals")
    return reasons


def generation_gate(dataset, payload):
    cases = dataset["cases"]
    rows = payload.get("results", [])
    ids = [r["case_id"] for r in rows]
    expected = {c["case_id"] for c in cases}
    errors = []
    if len(ids) != len(set(ids)):
        errors.append("duplicate_result_ids")
    if set(ids) - expected:
        errors.append("unknown_result_ids")
    by_id = {r["case_id"]: r for r in rows}
    pages, claims, refusals, per_case = [], [], [], []
    for case in cases:
        cid = case["case_id"]
        row = by_id.get(cid, {})
        issues = []
        human_reviews = [r for r in row.get("reviewers", []) if r.get("kind") == "human" and r.get("reviewer_id") and r.get("reviewed_at")]
        if len({r["reviewer_id"] for r in human_reviews}) < 2:
            issues.append("missing_two_human_generation_reviews")
        canonical = {"citation_reviews": row.get("citation_reviews", []),
                     "claim_faithfulness_scores": row.get("claim_faithfulness_scores", []),
                     "required_point_coverage": row.get("required_point_coverage", [])}
        for review in human_reviews:
            for field, value in canonical.items():
                if review.get(field) != value and not row.get("adjudication_notes"):
                    issues.append("independent_reviews_disagree_without_adjudication")
        answerable = case.get("is_answerable", case.get("should_answer"))
        if not row or row.get("error") or row.get("status") not in {None, "ok"}:
            issues.append("missing_or_failed_generation")
        if not answerable:
            ok = (bool(row.get("answer", "").strip()) and row.get("insufficient_evidence") is True
                  and row.get("citations") == [] and row.get("claims", []) == [] and not issues)
            refusals.append(bool(ok))
            if not ok:
                issues.append("not_a_valid_no_citation_refusal")
            if any(r.get("refusal_correct") is not True for r in human_reviews[:2]):
                issues.append("human_review_did_not_confirm_refusal")
        else:
            if row.get("insufficient_evidence") or not row.get("answer") or not row.get("claims") or not row.get("citations"):
                issues.append("answerable_case_unanswered_or_uncited")
            citations = row.get("citations", [])
            reviews = row.get("citation_reviews", [])
            if len(reviews) != len(citations) or not citations:
                issues.append("incomplete_citation_reviews")
            for i in range(max(1, len(citations))):
                r = reviews[i] if i < len(reviews) else {}
                valid = i < len(citations) and r.get("citation_sha256") == digest(citations[i]) and type(r.get("page_correct")) is bool
                pages.append(bool(valid and r["page_correct"]))
                if not valid:
                    issues.append("unbound_or_invalid_citation_review")
            scores = row.get("claim_faithfulness_scores", [])
            valid_scores = bool(scores) and len(scores) >= len(row.get("claims", [])) and all(type(s) in (int, float) and s in (0, 1) for s in scores)
            if not valid_scores or row.get("answer_assertions_exhaustively_reviewed") is not True:
                issues.append("missing_or_invalid_exhaustive_claim_reviews")
                claims.append(0.0)
            else:
                claims.extend(scores)
            points = row.get("required_point_coverage", [])
            if len(points) != len(case.get("required_points", [])) or not points or not all(type(p) is bool and p for p in points):
                issues.append("required_points_not_fully_covered")
        response = {k: row.get(k) for k in ("answer", "claims", "citations", "insufficient_evidence")}
        if row.get("reviewed_response_sha256") != digest(response):
            issues.append("review_not_bound_to_response")
        if not answerable and any(r.get("agrees_with_scores") is not True for r in human_reviews[:2]):
            issues.append("human_review_missing_agreement")
        if answerable and any(r.get("agrees_with_scores") is not True for r in human_reviews[:2]):
            issues.append("human_review_missing_agreement")
        per_case.append({"case_id": cid, "failures": sorted(set(issues))})
        errors.extend(f"{cid}:{issue}" for issue in sorted(set(issues)))
    mean = lambda values: sum(values) / len(values) if values else 0.0
    metrics = {"citation_page_accuracy": mean(pages), "faithfulness": mean(claims), "unanswerable_refusal_rate": mean(refusals)}
    limits = {"citation_page_accuracy": 0.90, "faithfulness": 0.85, "unanswerable_refusal_rate": 1.0}
    limits = {k: max(v, dataset.get("thresholds", {}).get(k, v)) for k, v in limits.items()}
    checks = {k: metrics[k] >= v for k, v in limits.items()}
    provenance = payload.get("acceptance", {})
    if provenance.get("classification") != "quality_acceptance" or provenance.get("eligible") is not True:
        errors.append("mechanism_test_or_missing_eligibility")
    if provenance.get("dataset_sha256") != digest(dataset):
        errors.append("dataset_provenance_mismatch")
    if provenance.get("generator_kind") not in {"openai-compatible", "transformers-local"}:
        errors.append("demo_or_missing_generator_identity")
    return {"passed": all(checks.values()) and not errors, "metrics_passed": all(checks.values()),
            "metrics": metrics, "thresholds": limits, "checks": checks, "blockers": errors, "cases": per_case,
            "denominators": {"citation_reviews": len(pages), "assertion_reviews": len(claims), "unanswerable": len(refusals)},
            "classification": provenance.get("classification", "mechanism_test")}
