import type { Book, Chapter, Chunk, DistillationKind, EvaluationDatasetSummary, EvaluationResult, HealthResponse, JobList, JobView, RetrieveAndAnswerResponse, RetrievalResponse, UploadAccepted } from "./bookdistill-contracts";

const ENV_API_BASE = process.env.NEXT_PUBLIC_BOOKDISTILL_API_URL?.replace(/\/$/, "") ?? "";

export class BookDistillApiError extends Error {
  constructor(public status:number, message:string, public code?:string, public traceId?:string) { super(message); }
}

export type ModelProfile = {
  profile_id:string;
  name:string;
  provider:string;
  model:string;
  base_url:string;
  response_format:"json_object"|"json_schema"|"tool_calling"|"text_parser";
  has_api_key:boolean;
};
export type ModelConfiguration = {active_profile_id:string;profiles:ModelProfile[]};
export type ModelProfileInput = Omit<ModelProfile,"has_api_key"|"profile_id"> & {profile_id?:string;api_key:string};

export function getApiBase():string {
  if (typeof window === "undefined") return ENV_API_BASE;
  const saved=window.localStorage.getItem("bookdistill.apiBase")?.replace(/\/$/, "");
  if (saved) return saved;
  if (ENV_API_BASE) return ENV_API_BASE;
  return window.location.hostname==="localhost"||window.location.hostname==="127.0.0.1" ? "http://127.0.0.1:8000" : "";
}

export function setApiBase(value:string) { if(typeof window!=="undefined") window.localStorage.setItem("bookdistill.apiBase",value.trim().replace(/\/$/,"")); }

export function hasApiKey():boolean { return typeof window!=="undefined" && Boolean(window.sessionStorage.getItem("bookdistill.apiKey")); }
export function setApiKey(value:string) { if(typeof window!=="undefined") { if(value.trim()) window.sessionStorage.setItem("bookdistill.apiKey",value.trim()); else window.sessionStorage.removeItem("bookdistill.apiKey"); } }

export async function verifyApiKey(value:string):Promise<void> {
  let response:Response;
  try {
    response=await fetch(`${getApiBase()}/api/v1/jobs?limit=1`,{
      headers:{Accept:"application/json","X-BookDistill-API-Key":value.trim()},
    });
  } catch {
    throw new Error("无法连接后端，请检查系统状态中的 API 地址。");
  }
  if(response.status===401) throw new Error("API Key 不正确，请检查 BOOKDISTILL_API_KEY。");
  if(!response.ok) throw new Error(`密钥验证失败（HTTP ${response.status}）。`);
}

async function request<T>(path:string, init?:RequestInit):Promise<T> {
  const apiKey=typeof window!=="undefined"?window.sessionStorage.getItem("bookdistill.apiKey"):null;
  const response=await fetch(`${getApiBase()}${path}`,{...init,headers:{Accept:"application/json",...(apiKey?{"X-BookDistill-API-Key":apiKey}:{}),...(init?.headers??{})}});
  if(!response.ok){let body:unknown;try{body=await response.json();}catch{body=null}const value=(body??{}) as {code?:string;message?:string;detail?:string|{code?:string};trace_id?:string};const nestedCode=typeof value.detail==="object"?value.detail?.code:undefined;const detail=typeof value.detail==="string"?value.detail:nestedCode;throw new BookDistillApiError(response.status,value.message??detail??`请求失败 (${response.status})`,value.code??nestedCode,value.trace_id);}
  if(response.status===204)return undefined as T;
  return response.json() as Promise<T>;
}

export const bookDistillApi={
  getModelConfiguration:()=>request<ModelConfiguration>("/api/v1/model-config"),
  saveModelProfile:(profile:ModelProfileInput)=>request<ModelProfile>("/api/v1/model-config/profiles",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(profile)}),
  activateModelProfile:(profileId:string)=>request<{active_profile_id:string}>("/api/v1/model-config/active",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile_id:profileId})}),
  deleteModelProfile:(profileId:string)=>request<void>(`/api/v1/model-config/profiles/${encodeURIComponent(profileId)}`,{method:"DELETE"}),
  listBooks:()=>request<Book[]>("/api/v1/books"),
  getBook:(bookId:string)=>request<Book>(`/api/v1/books/${bookId}`),
  getChapters:(bookId:string)=>request<Chapter[]>(`/api/v1/books/${bookId}/chapters`),
  getChunks:(bookId:string)=>request<Chunk[]>(`/api/v1/books/${bookId}/chunks`),
  uploadBook:(file:File, metadata:{title?:string;authors?:string;language?:string}={})=>{const form=new FormData();form.set("file",file);if(metadata.title)form.set("title",metadata.title);form.set("authors",metadata.authors??"");form.set("language",metadata.language??"zh-CN");return request<UploadAccepted>("/api/v1/books",{method:"POST",body:form});},
  listJobs:(status?:string)=>request<JobList>(`/api/v1/jobs?limit=100${status?`&status=${encodeURIComponent(status)}`:""}`),
  getJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}`),
  retryJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}/retry`,{method:"POST"}),
  cancelJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}/cancel`,{method:"POST"}),
  retrieve:(query:string, bookIds:string[], chapterIds:string[]=[])=>request<RetrievalResponse>("/api/v1/retrieval/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:"1.0",request_id:crypto.randomUUID(),query,scope:{book_ids:bookIds,chapter_ids:chapterIds}})}),
  ask:(question:string, bookIds:string[], chapterIds:string[]=[])=>{const requestId=crypto.randomUUID();return request<RetrieveAndAnswerResponse>("/api/v1/rag/answer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:"1.0",trace_context:{request_id:requestId,trace_id:crypto.randomUUID()},question,scope:{book_ids:bookIds,chapter_ids:chapterIds},language:"bilingual"})});},
  distill:(kind:DistillationKind,payload:unknown)=>request<Record<string,unknown>>(`/api/v1/generation/${kind}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}),
  health:()=>request<HealthResponse>("/api/v1/health/ready"),
  listEvaluationDatasets:()=>request<EvaluationDatasetSummary[]>("/api/v1/evaluation/datasets"),
  saveEvaluationDataset:(dataset:unknown)=>request<EvaluationDatasetSummary>("/api/v1/evaluation/datasets",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(dataset)}),
  listEvaluationRuns:()=>request<EvaluationResult[]>("/api/v1/evaluation/runs"),
  runEvaluation:(datasetKey:string,datasetVersion:string)=>request<EvaluationResult>("/api/v1/evaluation/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dataset_key:datasetKey,experiment:{schema_version:"1.0",experiment_id:`ui-${Date.now()}`,dataset_version:datasetVersion,retrieval_variant:"hybrid_rerank",parser_version:"pymupdf-v1",chunk_version:"current",embedding_model:"memory-hash-v1",sparse_config:{},fusion_config:{strategy:"rrf"},reranker_model:"lexical-v1",generation_config:{},notes:"Created from product interface"}})}),
};
