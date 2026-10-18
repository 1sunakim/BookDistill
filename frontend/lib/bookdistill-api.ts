import type { AgentRunView, Book, Chapter, Chunk, DistillationKind, EvaluationDatasetSummary, EvaluationResult, HealthResponse, ImageAssetList, ImageAssetView, JobList, JobView, ObsidianExportOptions, RetrieveAndAnswerResponse, RetrievalResponse, SourceSearchResponse, TextBook, TextBookAccepted, TextChapter, TextChunk, UploadAccepted } from "./bookdistill-contracts";

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

export const CONNECTION_CHANGE_EVENT="bookdistill:connection-change";
export function setApiBase(value:string) { if(typeof window!=="undefined") {window.localStorage.setItem("bookdistill.apiBase",value.trim().replace(/\/$/,""));window.dispatchEvent(new Event(CONNECTION_CHANGE_EVENT));} }

export function getApiKey():string { return typeof window!=="undefined"?window.sessionStorage.getItem("bookdistill.apiKey")??"":""; }
export function hasApiKey():boolean { return Boolean(getApiKey()); }
export function setApiKey(value:string) { if(typeof window!=="undefined") { if(value.trim()) window.sessionStorage.setItem("bookdistill.apiKey",value.trim()); else window.sessionStorage.removeItem("bookdistill.apiKey");window.dispatchEvent(new Event(CONNECTION_CHANGE_EVENT)); } }

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

async function fetchResponse(path:string, init?:RequestInit):Promise<Response> {
  const apiKey=typeof window!=="undefined"?window.sessionStorage.getItem("bookdistill.apiKey"):null;
  const headers=new Headers(init?.headers);
  if(!headers.has("Accept"))headers.set("Accept","application/json");
  if(apiKey)headers.set("X-BookDistill-API-Key",apiKey);
  let response:Response;
  try{response=await fetch(`${getApiBase()}${path}`,{...init,headers});}
  catch(error){if(init?.signal?.aborted)throw error;throw new Error("无法连接后端，请在系统状态中检查 API 地址与网络连接。");}
  if(!response.ok){let body:unknown;try{body=await response.json();}catch{body=null}const value=(body??{}) as {code?:string;message?:string;detail?:string|{code?:string};trace_id?:string};const nestedCode=typeof value.detail==="object"?value.detail?.code:undefined;const detail=typeof value.detail==="string"?value.detail:nestedCode;throw new BookDistillApiError(response.status,value.message??detail??`请求失败 (${response.status})`,value.code??nestedCode,value.trace_id);}
  return response;
}

async function request<T>(path:string, init?:RequestInit):Promise<T> {
  const response=await fetchResponse(path,init);
  if(response.status===204)return undefined as T;
  return response.json() as Promise<T>;
}

async function requestBlob(path:string, kind:"image"|"zip", signal?:AbortSignal):Promise<Blob> {
  const response=await fetchResponse(path,{signal,cache:"no-store",headers:{Accept:kind==="zip"?"application/zip":"image/png,image/jpeg,image/webp"}});
  const mime=response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase();
  const validMime=kind==="zip"?mime==="application/zip":Boolean(mime&&["image/png","image/jpeg","image/webp"].includes(mime));
  if(!validMime)throw new Error("后端返回的文件格式不正确，未开始下载。");
  const blob=await response.blob();
  if(!blob.size)throw new Error("后端返回了空文件，请重试。");
  return blob;
}

export function privateContentError(error:unknown):string {
  if(error instanceof BookDistillApiError){
    const messages:Record<string,string>={
      CONTENT_KEY_REQUIRED:"服务器尚未配置后端访问 Key，请设置 BOOKDISTILL_API_KEY 后重启服务。",
      AUTHENTICATION_REQUIRED:"访问 Key 缺失或不正确，请点击“配置访问 Key”后重试。",
      BOOK_TEXT_NOT_READY:"书籍尚无可导出的文本和章节。图片仍可查看；请在系统状态中查看处理任务。",
      ARTIFACT_NOT_FOUND:"没有找到指定的保存产物，请核对产物 ID。",
      ARTIFACT_SCOPE_MISMATCH:"指定产物的来源不包含本书，请选择与本书相关的保存产物。",
      ARTIFACT_SOURCES_INVALID:"保存产物的来源引用不完整，无法导出，请核对保存内容。",
      EXPORT_SOURCE_CHANGED:"导出期间内容发生变化，请稍后重新导出。",
      BOOK_NOT_FOUND:"书籍已不存在，请刷新书籍库。",
      IMAGE_NOT_FOUND:"图片已不存在，请刷新图片列表。",
      IMAGE_CONTENT_NOT_FOUND:"图片文件缺失，请检查存储或重新处理来源 PDF。",
      IMAGE_STORAGE_ERROR:"无法安全读取或保存图片，请检查服务器存储后重试。",
      EXPORT_STORAGE_ERROR:"无法完成导出，请检查服务器存储后重试。",
      IMAGE_TOO_LARGE:"图片超过服务器的文件大小或像素限制，请选择较小的图片。",
      UNSUPPORTED_IMAGE_FORMAT:"请选择 PNG、JPEG 或 WebP 图片。",
      INVALID_IMAGE:"图片损坏或属于多帧/动画图片，请选择有效的静态图片。",
    };
    const message=messages[error.code??""]??error.message;
    return error.traceId?`${message}（追踪 ID：${error.traceId}）`:message;
  }
  return error instanceof Error?error.message:String(error);
}

export const bookDistillApi={
  getModelConfiguration:()=>request<ModelConfiguration>("/api/v1/model-config"),
  saveModelProfile:(profile:ModelProfileInput)=>request<ModelProfile>("/api/v1/model-config/profiles",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(profile)}),
  activateModelProfile:(profileId:string)=>request<{active_profile_id:string}>("/api/v1/model-config/active",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile_id:profileId})}),
  deleteModelProfile:(profileId:string)=>request<void>(`/api/v1/model-config/profiles/${encodeURIComponent(profileId)}`,{method:"DELETE"}),
  listBooks:()=>request<Book[]>("/api/v1/books"),
  getBook:(bookId:string)=>request<Book>(`/api/v1/books/${bookId}`),
  getChapters:(bookId:string,signal?:AbortSignal)=>request<Chapter[]>(`/api/v1/books/${encodeURIComponent(bookId)}/chapters`,{signal}),
  getChunks:(bookId:string,signal?:AbortSignal)=>request<Chunk[]>(`/api/v1/books/${encodeURIComponent(bookId)}/chunks`,{signal}),
  getBookImages:(bookId:string,signal?:AbortSignal)=>request<ImageAssetView[]>(`/api/v1/books/${encodeURIComponent(bookId)}/images`,{signal,cache:"no-store"}),
  listUploadedImages:(limit=24,offset=0,signal?:AbortSignal)=>request<ImageAssetList>(`/api/v1/images?limit=${limit}&offset=${offset}`,{signal,cache:"no-store"}),
  uploadImage:(file:File,signal?:AbortSignal)=>{const form=new FormData();form.set("file",file);return request<ImageAssetView>("/api/v1/images",{method:"POST",body:form,signal});},
  imageContent:(assetId:string,signal?:AbortSignal)=>requestBlob(`/api/v1/images/${encodeURIComponent(assetId)}/content`,"image",signal),
  exportObsidian:(bookId:string,options:ObsidianExportOptions={},signal?:AbortSignal)=>{
    const artifactIds=[...new Set(options.artifactIds??[])];
    if(artifactIds.length>100)throw new Error("每次最多选择 100 个不同的产物 ID。");
    const query=new URLSearchParams({book_id:bookId,include_pdf:String(options.includePdf??false)});
    for(const id of artifactIds)query.append("artifact_id",id);
    return requestBlob(`/api/v1/exports/obsidian?${query}`,"zip",signal);
  },
  uploadBook:(file:File, metadata:{title?:string;authors?:string;language?:string}={})=>{const form=new FormData();form.set("file",file);if(metadata.title)form.set("title",metadata.title);form.set("authors",metadata.authors??"");form.set("language",metadata.language??"zh-CN");return request<UploadAccepted>("/api/v1/books",{method:"POST",body:form});},
  listTextBooks:()=>request<TextBook[]>("/api/v2/books"),
  getTextChapters:(bookId:string,signal?:AbortSignal)=>request<TextChapter[]>(`/api/v2/books/${encodeURIComponent(bookId)}/chapters`,{signal}),
  getTextChunks:(bookId:string,signal?:AbortSignal)=>request<TextChunk[]>(`/api/v2/books/${encodeURIComponent(bookId)}/chunks`,{signal}),
  uploadTextBook:(file:File,metadata:{title?:string;authors?:string;language?:string}={})=>{const form=new FormData();form.set("file",file);if(metadata.title)form.set("title",metadata.title);form.set("authors",metadata.authors??"");form.set("language",metadata.language??"zh-CN");return request<TextBookAccepted>("/api/v2/books",{method:"POST",body:form});},
  searchSourceBooks:(query:string,bookIds:string[],chapterIds:string[]=[])=>request<SourceSearchResponse>("/api/v2/retrieval/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:"2.0",request_id:crypto.randomUUID(),query,book_ids:bookIds,chapter_ids:chapterIds})}),
  reprocessTextBook:(bookId:string,fromStage:"parse"|"index"="parse")=>request<JobView>(`/api/v2/books/${encodeURIComponent(bookId)}/reprocess`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({from_stage:fromStage})}),
  deleteTextBook:(bookId:string)=>request<void>(`/api/v2/books/${encodeURIComponent(bookId)}`,{method:"DELETE"}),
  exportSourceObsidian:(bookId:string,includeOriginal=true)=>requestBlob(`/api/v2/exports/obsidian?book_id=${encodeURIComponent(bookId)}&include_original=${includeOriginal}`,"zip"),
  listJobs:(status?:string)=>request<JobList>(`/api/v1/jobs?limit=100${status?`&status=${encodeURIComponent(status)}`:""}`),
  getJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}`),
  retryJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}/retry`,{method:"POST"}),
  cancelJob:(jobId:string)=>request<JobView>(`/api/v1/jobs/${jobId}/cancel`,{method:"POST"}),
  retrieve:(query:string, bookIds:string[], chapterIds:string[]=[])=>request<RetrievalResponse>("/api/v1/retrieval/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:"1.0",request_id:crypto.randomUUID(),query,scope:{book_ids:bookIds,chapter_ids:chapterIds}})}),
  ask:(question:string, bookIds:string[], chapterIds:string[]=[])=>{const requestId=crypto.randomUUID();return request<RetrieveAndAnswerResponse>("/api/v1/rag/answer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:"1.0",trace_context:{request_id:requestId,trace_id:crypto.randomUUID()},question,scope:{book_ids:bookIds,chapter_ids:chapterIds},language:"bilingual"})});},
  createResearchRun:(question:string,bookIds:string[],questionCount:number,idempotencyKey:string)=>request<AgentRunView>("/api/v1/agent/runs",{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":idempotencyKey},body:JSON.stringify({question,book_ids:bookIds,question_count:questionCount})}),
  getResearchRun:(runId:string)=>request<AgentRunView>(`/api/v1/agent/runs/${encodeURIComponent(runId)}`),
  cancelResearchRun:(runId:string)=>request<AgentRunView>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/cancel`,{method:"POST"}),
  retryResearchRun:(runId:string)=>request<AgentRunView>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/retry`,{method:"POST"}),
  distill:(kind:DistillationKind,payload:unknown)=>request<Record<string,unknown>>(`/api/v1/generation/${kind}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}),
  distillSaved:async(kind:DistillationKind,payload:unknown)=>{
    const response=await fetchResponse(`/api/v1/generation/${kind}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    return {content:await response.json() as Record<string,unknown>,artifactId:response.headers.get("X-Artifact-ID")};
  },
  health:()=>request<HealthResponse>("/api/v1/health/ready"),
  listEvaluationDatasets:()=>request<EvaluationDatasetSummary[]>("/api/v1/evaluation/datasets"),
  saveEvaluationDataset:(dataset:unknown)=>request<EvaluationDatasetSummary>("/api/v1/evaluation/datasets",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(dataset)}),
  listEvaluationRuns:()=>request<EvaluationResult[]>("/api/v1/evaluation/runs"),
  runEvaluation:(datasetKey:string,datasetVersion:string)=>request<EvaluationResult>("/api/v1/evaluation/runs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dataset_key:datasetKey,experiment:{schema_version:"1.0",experiment_id:`ui-${Date.now()}`,dataset_version:datasetVersion,retrieval_variant:"hybrid_rerank",parser_version:"pymupdf-v1",chunk_version:"current",embedding_model:"memory-hash-v1",sparse_config:{},fusion_config:{strategy:"rrf"},reranker_model:"lexical-v1",generation_config:{},notes:"Created from product interface"}})}),
};
