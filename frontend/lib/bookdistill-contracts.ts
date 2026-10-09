export type BookStatus = "uploaded"|"validating"|"parsing"|"structuring"|"chunking"|"indexing"|"ready"|"failed"|"cancelled";

export interface Book {
  schema_version:"1.0"; book_id:string; title:string; authors:string[]; language:string;
  description:string|null; tags:string[]; original_file_name:string; mime_type:"application/pdf";
  size_bytes:number; sha256:string; storage_uri:string; page_count:number; status:BookStatus;
  created_at:string; updated_at:string;
}

export type ImageBoundingBox = {x0:number;y0:number;x1:number;y1:number};
export type DocumentResource =
  | {type:"pdf_toc";entries:{level:number;title:string;page_index:number}[]}
  | {type:"image";asset_id:string;page_index:number;bbox:ImageBoundingBox};
export interface ParsedTextBlock {
  schema_version:"1.0";block_id:string;page_index:number;block_index:number;reading_order:number;
  content_type:string;raw_text:string;text:string;bbox:ImageBoundingBox;
  style:{font_name:string|null;font_size:number|null;bold:boolean|null;italic:boolean|null}|null;
  source:"text_layer"|"ocr";confidence:number|null;
}
export interface ParsedPage {
  schema_version:"1.0";page_index:number;printed_page_number:string|null;width:number;height:number;
  rotation:0|90|180|270;page_type:"text"|"scanned"|"mixed";ocr_used:boolean;quality_flags:string[];
  blocks:ParsedTextBlock[];
}
export interface ParsedDocument {
  schema_version:"1.0"|"1.1";document_id:string;book_id:string;parser_name:string;parser_version:string;
  parse_config_sha256:string;pages:ParsedPage[];resources:DocumentResource[];created_at:string;
}
export interface Chapter { schema_version:"1.0"; chapter_id:string; book_id:string; parent_id:string|null; title:string; normalized_title:string; level:number; order_index:number; page_start:number; page_end:number; source_block_id:string; confidence:number; manually_verified:boolean; }
export interface Chunk { schema_version:"1.0"; chunk_id:string; book_id:string; chapter_id:string; chapter_path:string[]; text:string; raw_text:string; page_start:number; page_end:number; source_block_ids:string[]; previous_chunk_id:string|null; next_chunk_id:string|null; token_count:number; content_type:string; chunk_version:string; checksum:string; }

export interface Evidence { schema_version:"1.0"; evidence_id:string; chunk_id:string; book_id:string; book_title:string|null; chapter_id:string; chapter_path:string[]; page_start:number; page_end:number; text:string; dense_score:number|null; dense_rank:number|null; sparse_score:number|null; sparse_rank:number|null; fusion_score:number; fusion_rank:number; rerank_score:number|null; final_rank:number; retrieval_version:string; context_chunk_ids:string[]; context_expanded:boolean; estimated_tokens:number; }
export interface CandidateTrace { chunk_id:string; dense_score:number|null; dense_rank:number|null; sparse_score:number|null; sparse_rank:number|null; fusion_score:number; fusion_rank:number; rerank_score:number|null; final_rank:number|null; }
export type RetrievalStageStatus="skipped"|"hit"|"empty"|"timeout"|"error";
export interface RetrievalTrace { schema_version:"1.0"; original_query:string; normalized_query:string; retrieval_version:string; indexed_chunk_count:number; dense_latency_ms:number; sparse_latency_ms:number; fusion_latency_ms:number; rerank_latency_ms:number; total_latency_ms:number; rerank_degraded:boolean; rerank_applied:boolean; dense_status:RetrievalStageStatus; sparse_status:RetrievalStageStatus; rerank_status:RetrievalStageStatus; dense_hit_count:number; sparse_hit_count:number; rerank_input_count:number; dense_degraded:boolean; sparse_degraded:boolean; dense_timeout_ms:number; sparse_timeout_ms:number; rerank_timeout_ms:number; index_versions:string[]; model_names:string[]; chunk_versions:string[]; index_generated_at:string|null; context_token_count:number; candidates:CandidateTrace[]; }
export interface RetrievalResponse { schema_version:"1.0"; request_id:string; evidence:Evidence[]; trace:RetrievalTrace; }

export interface Citation { evidence_id:string; chunk_id:string; book_id:string; book_title:string|null; chapter_id:string; chapter_path:string[]; page_start:number; page_end:number; quote:string; }
export interface RagAnswerResponse { schema_version:"1.0"; answer:string; claims:{text:string;evidence_ids:string[]}[]; citations:Citation[]; insufficient_evidence:boolean; follow_up:string|null; model_provider:string; model_name:string; prompt_version:string; usage:{input_tokens:number|null;output_tokens:number|null}; latency_ms:number; provider_request_id:string; repair_attempted:boolean; created_at:string; }
export interface RetrieveAndAnswerResponse { schema_version:"1.0"; retrieval:RetrievalResponse; answer:RagAnswerResponse; }

export interface JobError { code:string; message:string; retryable:boolean; details:Record<string,unknown>; }
export interface JobView { schema_version:"1.0"; job_id:string; parent_job_id:string|null; job_type:string; subject_id:string|null; status:"pending"|"queued"|"running"|"succeeded"|"failed"|"retrying"|"cancel_requested"|"cancelled"; progress_current:number; progress_total:number; attempt:number; max_attempts:number; input_reference:Record<string,unknown>; output_reference:Record<string,unknown>; checkpoint:Record<string,unknown>; idempotency_key:string; error:JobError|null; worker_id:string|null; trace_id:string; created_at:string; queued_at:string|null; started_at:string|null; finished_at:string|null; heartbeat_at:string|null; stages:{stage_run_id:string;stage_name:string;status:string;attempt:number;error:JobError|null;started_at:string|null;finished_at:string|null}[]; }
export interface JobList { items:JobView[]; total:number; limit:number; offset:number; }
export interface HealthResponse { status:"ok"|"degraded"; service?:string; checks:Record<string,string>; timestamp:string; }
export interface UploadAccepted { book_id:string; job_id:string; task_id:string|null; duplicate:boolean; }

export interface EvaluationDatasetSummary { dataset_key:string; dataset_name:string; dataset_version:string; split:string; case_count:number; description:string|null; }
export interface EvaluationResult { schema_version:"1.0"; experiment:{experiment_id:string;dataset_version:string;retrieval_variant:string;parser_version:string;chunk_version:string}; dataset_name:string; dataset_version:string; created_at:string; aggregate:{evaluated_cases:number;excluded_unanswerable_cases:number;missing_or_failed_predictions:number;mrr:number;ndcg_at_10:number;cutoffs:{k:number;recall:number;precision:number}[];latency_p50_ms:number;latency_p95_ms:number}; cases:{case_id:string;question_type:string;first_relevant_rank:number|null;passed_at_10:boolean;error:string|null}[]; }

export type DistillationKind = "summary"|"knowledge-points"|"questions"|"viewpoint-comparison";
export interface ApiErrorPayload { code?:string; message?:string; detail?:string|Record<string,unknown>; trace_id?:string; }
