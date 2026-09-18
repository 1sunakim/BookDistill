import type { Book, Chapter, Chunk, DistillationKind, EvaluationDatasetSummary, EvaluationResult, HealthResponse, JobList, JobView, RetrieveAndAnswerResponse, RetrievalResponse, UploadAccepted } from "./bookdistill-contracts";

const ENV_API_BASE = process.env.NEXT_PUBLIC_BOOKDISTILL_API_URL?.replace(/\/$/, "") ?? "";

export class BookDistillApiError extends Error {
  constructor(public status:number, message:string, public code?:string, public traceId?:string) { super(message); }
}

export function getApiBase():string {
  if (typeof window === "undefined") return ENV_API_BASE;
  const saved=window.localStorage.getItem("bookdistill.apiBase")?.replace(/\/$/, "");
  if (saved) return saved;
  if (ENV_API_BASE) return ENV_API_BASE;
  return window.location.hostname==="localhost"||window.location.hostname==="127.0.0.1" ? "http://127.0.0.1:8000" : "";
}

export function setApiBase(value:string) { if(typeof window!=="undefined") window.localStorage.setItem("bookdistill.apiBase",value.trim().replace(/\/$/,"")); }

async function request<T>(path:string, init?:RequestInit):Promise<T> {
  const response=await fetch(`${getApiBase()}${path}`,{...init,headers:{Accept:"application/json",...(init?.headers??{})}});
  if(!response.ok){let body:unknown;try{body=await response.json();}catch{body=null}const value=(body??{}) as {code?:string;message?:string;detail?:string|{code?:string};trace_id?:string};const nestedCode=typeof value.detail==="object"?value.detail?.code:undefined;const detail=typeof value.detail==="string"?value.detail:nestedCode;throw new BookDistillApiError(response.status,value.message??detail??`请求失败 (${response.status})`,value.code??nestedCode,value.trace_id);}
  if(response.status===204)return undefined as T;
  return response.json() as Promise<T>;
}

export const bookDistillApi={
  listBooks:()=>request<Book[]>("/v1/books"),
  getBook:(bookId:string)=>request<Book>(`/v1/books/${bookId}`),
  getChapters:(bookId:string)=>request<Chapter[]>(`/v1/books/${bookId}/chapters`),
  getChunks:(bookId:string)=>request<Chunk[]>(`/v1/books/${bookId}/chunks`),
  uploadBook:(file:File, metadata:{title?:string;authors?:string;language?:string}={})=>{const form=new FormData();form.set("file",file);if(metadata.title)form.set("title",metadata.title);form.set("authors",metadata.authors??"");form.set("language",metadata.language??"zh-CN");return request<UploadAccepted>("/v1/books",{method:"POST",body:form});},
  listJobs:(status?:string)=>request<JobList>(`/api/v1/jobs?limit=100${status?`&status=${encodeURIComponent(status)}`:""}`),
  getJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}`),
  retryJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}/retry`,{method:"POST"}),
  cancelJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}/cancel`,{method:"POST"}),
  retrieve:(query:string, bookIds:string[], chapterIds:string[]=[])=>request<RetrievalResponse>("/api/v1/retrieval/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:"1.0",request_id:crypto.randomUUID(),query,scope:{book_ids:bookIds,chapter_ids:chapterIds}})}),
  ask:(question:string, bookIds:string[], chapterIds:string[]=[])=>{const requestId=crypto.randomUUID();return request<RetrieveAndAnswerResponse>("/api/v1/rag/answer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:"1.0",trace_context:{request_id:requestId,trace_id:crypto.randomUUID()},question,scope:{book_ids:bookIds,chapter_ids:chapterIds},language:"zh-CN"})});},
  distill:(kind:DistillationKind,payload:unknown)=>request<Record<string,unknown>>(`/api/v1/generation/${kind}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}),
  health:()=>request<HealthResponse>("/health/ready"),
  listEvaluationDatasets:()=>request<EvaluationDatasetSummary[]>("/api/v1/evaluation/datasets"),
  saveEvaluationDataset:(dataset:unknown)=>request<EvaluationDatasetSummary>("/api/v1/evaluation/datasets",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(dataset)}),
  listEvaluationRuns:()=>request<EvaluationResult[]>("/api/v1/evaluation/runs"),
  runEvaluation:(datasetKey:string,datasetVersion:string)=>request<EvaluationResult>("/api/v1/evaluation/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dataset_key:datasetKey,experiment:{schema_version:"1.0",experiment_id:`ui-${Date.now()}`,dataset_version:datasetVersion,retrieval_variant:"hybrid_rerank",parser_version:"pymupdf-v1",chunk_version:"current",embedding_model:"memory-hash-v1",sparse_config:{},fusion_config:{strategy:"rrf"},reranker_model:"lexical-v1",generation_config:{},notes:"Created from product interface"}})}),
};
