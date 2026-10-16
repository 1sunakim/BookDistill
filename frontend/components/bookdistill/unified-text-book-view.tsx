"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronRight, MessageSquareText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { bookDistillApi } from "@/lib/bookdistill-api";
import type { BookSourceLocation, JobView, SourceEvidence, TextBook, TextChapter, TextChunk } from "@/lib/bookdistill-contracts";
import { Cover, EmptyState, ErrorPanel, LoadingState } from "./common";

export function TextBookView({book,onChanged,onDeleted}:{book:TextBook;onChanged:()=>void;onDeleted:()=>void}) {
  const [chapters,setChapters]=useState<TextChapter[]>([]);
  const [chunks,setChunks]=useState<TextChunk[]>([]);
  const [jobs,setJobs]=useState<JobView[]>([]);
  const [chapterId,setChapterId]=useState("");
  const [section,setSection]=useState<"text"|"images">("text");
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const [revision,setRevision]=useState(0);
  const [query,setQuery]=useState("");
  const [hits,setHits]=useState<SourceEvidence[]>([]);
  const [searching,setSearching]=useState(false);
  const [searchError,setSearchError]=useState<string|null>(null);
  const searchInput=useRef<HTMLInputElement>(null);
  const readingPane=useRef<HTMLElement>(null);

  useEffect(()=>{
    const controller=new AbortController();
    Promise.all([bookDistillApi.getTextChapters(book.book_id,controller.signal),bookDistillApi.getTextChunks(book.book_id,controller.signal),bookDistillApi.listJobs()])
      .then(([chapterItems,chunkItems,jobList])=>{if(!controller.signal.aborted){setChapters(chapterItems);setChunks(chunkItems);setJobs(jobList.items);setError(null);setLoading(false)}})
      .catch(cause=>{if(!controller.signal.aborted){setError(cause instanceof Error?cause.message:String(cause));setLoading(false)}});
    return()=>controller.abort();
  },[book.book_id,book.status,revision]);

  const selected=chapters.find(chapter=>chapter.chapter_id===chapterId)??chapters[0];
  const chapterById=new Map(chapters.map(chapter=>[chapter.chapter_id,chapter]));
  const visible=selected?chunks.filter(chunk=>{let chapter=chapterById.get(chunk.chapter_id);const seen=new Set<string>();while(chapter&&!seen.has(chapter.chapter_id)){if(chapter.chapter_id===selected.chapter_id)return true;seen.add(chapter.chapter_id);chapter=chapter.parent_id?chapterById.get(chapter.parent_id):undefined}return false}):chunks;
  const selectedJob=jobs.find(job=>job.subject_id===book.book_id&&job.job_type==="document.process"&&job.input_reference.format===book.format);
  const selectChapter=(id:string)=>{setChapterId(id);setSection("text");readingPane.current?.scrollTo({top:0,behavior:"instant"});if(!window.matchMedia("(min-width: 1280px)").matches)readingPane.current?.scrollIntoView({behavior:"instant",block:"start"})};

  const search=async()=>{
    if(!query.trim()||!book.retrieval_ready)return;
    setSearching(true);setSearchError(null);
    try{const result=await bookDistillApi.searchSourceBooks(query.trim(),[book.book_id]);setHits(result.evidence)}
    catch(cause){setSearchError(cause instanceof Error?cause.message:String(cause))}
    finally{setSearching(false)}
  };
  const runAction=async(action:()=>Promise<unknown>)=>{
    setBusy(true);setError(null);
    try{await action();setRevision(value=>value+1);onChanged()}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  };
  const remove=async()=>{
    if(!window.confirm(`删除《${book.title}》及其原文件、章节和检索索引？`))return;
    await runAction(async()=>{await bookDistillApi.deleteTextBook(book.book_id);onDeleted()});
  };
  const exportBook=async()=>{
    setBusy(true);setError(null);
    try{const blob=await bookDistillApi.exportSourceObsidian(book.book_id);const url=URL.createObjectURL(blob);const link=document.createElement("a");link.href=url;link.download=`bookdistill-${book.book_id}.zip`;document.body.appendChild(link);try{link.click()}finally{link.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000)}}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  };

  return <div className="min-h-[calc(100vh-72px)] xl:grid xl:h-[calc(100dvh-72px)] xl:grid-cols-[280px_minmax(0,1fr)_270px] xl:overflow-hidden">
    <aside className="max-h-[40dvh] overflow-y-auto overscroll-contain border-r bg-white p-5 xl:max-h-none xl:min-h-0">
      <div className="mb-5 flex items-center gap-3"><Cover book={book} className="h-20 w-14"/><div className="min-w-0"><h2 className="line-clamp-2 text-sm font-semibold">{book.title}</h2><p className="mt-1 truncate text-xs text-muted-foreground">{book.authors?.join("、")||"作者未识别"}</p><p className="mt-1 text-xs text-muted-foreground">文件格式：{book.format.toUpperCase()}</p></div></div>
      <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">目录 · {chapters.length} 章</p>
      <div className="space-y-1">{chapters.map(chapter=><button key={chapter.chapter_id} onClick={()=>selectChapter(chapter.chapter_id)} style={{paddingLeft:`${12+Math.min(chapter.level-1,4)*16}px`}} className={`flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left text-sm ${selected?.chapter_id===chapter.chapter_id?"bg-secondary text-primary":"hover:bg-muted"}`}><ChevronRight className="mt-0.5 size-4 shrink-0"/><span className="min-w-0 flex-1"><span className="block break-words">{chapter.title}</span><span className="mt-1 block break-all text-xs opacity-60">{locationLabel(chapter.source_location)}</span></span></button>)}</div>
    </aside>
    <article ref={readingPane} className="scroll-mt-[72px] bg-[#fbfbfc] px-5 py-8 sm:px-10 lg:px-14 xl:min-h-0 xl:overflow-y-auto xl:overscroll-contain"><div className="mx-auto max-w-3xl">
      <div className="mb-6 flex gap-2" aria-label="书籍内容"><Button variant={section==="text"?"default":"outline"} aria-pressed={section==="text"} onClick={()=>setSection("text")}>章节原文</Button><Button variant={section==="images"?"default":"outline"} aria-pressed={section==="images"} onClick={()=>setSection("images")}>书中图片</Button></div>
      {error&&<ErrorPanel message={error}/>}
      {section==="text"&&<div className="mb-7 rounded-2xl border bg-white p-4"><div className="flex gap-2"><input ref={searchInput} value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==="Enter")void search()}} placeholder="检索本书原文" aria-label="检索本书原文" className="min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm"/><Button onClick={()=>void search()} disabled={!query.trim()||searching||!book.retrieval_ready}>{searching?"检索中…":"检索"}</Button></div>{searchError&&<p className="mt-2 text-sm text-destructive">{searchError}</p>}{hits.length>0&&<div className="mt-3 space-y-2">{hits.map(hit=><button key={hit.evidence_id} onClick={()=>selectChapter(hit.chapter_id)} className="block w-full rounded-lg border p-3 text-left text-sm hover:bg-muted"><span className="font-medium">{hit.chapter_path.join(" / ")}</span><span className="ml-2 text-muted-foreground">{locationRange(hit.source_start,hit.source_end)}</span><span className="mt-1 block line-clamp-2">{hit.text}</span></button>)}</div>}</div>}
      {section==="images"?<EmptyState title="暂无可展示的书中图片" description="当前文档处理流程未提供独立图片资产。"/>:loading?<LoadingState label="正在加载章节与原文块"/>:<>
        <div className="mb-7 flex items-start justify-between gap-3 border-b pb-5"><div><p className="text-xs font-medium text-primary">{selected?`第 ${selected.order_index+1} 章 · 层级 ${selected.level}`:"已切分原文"}</p><h2 className="mt-2 text-2xl font-semibold">{selected?.title??book.title}</h2></div>{selected&&<span className="shrink-0 rounded-lg border bg-white px-3 py-1.5 text-sm text-muted-foreground">{locationLabel(selected.source_location)}</span>}</div>
        {visible.length?<div className="space-y-5">{visible.map(chunk=><section key={chunk.chunk_id} className="chunk-block"><div className="chunk-label">原文 · {locationRange(chunk.source_start,chunk.source_end)} · {chunk.token_count} tokens</div><p className="whitespace-pre-wrap text-[16px] leading-8 text-[#323b4d]">{chunk.text}</p><details className="mt-4 border-t pt-3 text-sm"><summary className="cursor-pointer text-primary">查看原文片段</summary><pre className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-muted p-3">{chunk.raw_text}</pre></details></section>)}</div>:<EmptyState title={book.status==="failed"?"文本处理失败":"此章节暂无文本块"} description={book.status==="failed"?"请查看右侧任务状态与错误信息。":"章节与正文处理完成后即可阅读。"}/>}
      </>}
    </div></article>
    <aside className="border-l bg-white p-5 xl:min-h-0 xl:overflow-y-auto"><Button onClick={()=>{setSection("text");window.setTimeout(()=>{searchInput.current?.scrollIntoView({behavior:"smooth",block:"center"});searchInput.current?.focus()},0)}} disabled={!book.retrieval_ready} className="w-full rounded-xl"><MessageSquareText className="size-4"/>检索本书原文</Button><div className="mt-7 space-y-4 text-sm"><Metric label="状态" value={book.status}/><Metric label="章节" value={String(chapters.length)}/><Metric label="文本块" value={String(chunks.length)}/><Metric label="格式" value={book.format.toUpperCase()}/><Metric label="协议" value={book.schema_version}/></div><div className="mt-8 border-t pt-7"><h3 className="font-semibold">导出到 Obsidian</h3><p className="mt-3 text-sm leading-6 text-muted-foreground">下载章节、原文与原始文件，解压到 vault 根目录即可阅读。</p><Button className="mt-5 w-full" disabled={busy||!book.retrieval_ready} onClick={()=>void exportBook()}>下载 Obsidian ZIP</Button></div><div className="mt-8 space-y-2 border-t pt-7"><h3 className="font-semibold">书籍管理</h3>{selectedJob?.status==="failed"&&<p className="text-sm text-destructive">{selectedJob.error?.message??"处理任务失败"}</p>}{selectedJob?.status==="failed"&&<Button variant="outline" className="w-full" disabled={busy} onClick={()=>void runAction(()=>bookDistillApi.retryJob(selectedJob.job_id))}>重试任务</Button>}{selectedJob&&["queued","running","retrying"].includes(selectedJob.status)&&<Button variant="outline" className="w-full" disabled={busy} onClick={()=>void runAction(()=>bookDistillApi.cancelJob(selectedJob.job_id))}>取消任务</Button>}<Button variant="outline" className="w-full" disabled={busy||book.status==="queued"||book.status==="processing"} onClick={()=>void runAction(()=>bookDistillApi.reprocessTextBook(book.book_id,"index"))}>重建检索索引</Button><Button variant="outline" className="w-full" disabled={busy||book.status==="queued"||book.status==="processing"} onClick={()=>void runAction(()=>bookDistillApi.reprocessTextBook(book.book_id,"parse"))}>重新解析</Button><Button variant="outline" className="w-full" disabled={busy||book.status==="queued"||book.status==="processing"} onClick={()=>void remove()}>删除书籍</Button></div></aside>
  </div>;
}

function Metric({label,value}:{label:string;value:string}){return <div className="flex justify-between border-b pb-3"><span className="text-muted-foreground">{label}</span><span className="font-medium">{value}</span></div>}
function locationLabel(location:BookSourceLocation):string{if(location.kind==="text_line")return `第 ${location.line_start} 行`;if(location.kind==="docx_block")return `段落 ${location.block_index+1}`;return `${location.href}${location.anchor?`#${location.anchor}`:""} · 块 ${location.block_index+1}`}
function locationRange(start:BookSourceLocation,end:BookSourceLocation):string{if(start.kind==="text_line"&&end.kind==="text_line")return `原文第 ${start.line_start}–${end.line_end} 行`;if(start.kind==="docx_block"&&end.kind==="docx_block")return `原文段落/表格 ${start.block_index+1}–${end.block_index+1}`;return `${locationLabel(start)} → ${locationLabel(end)}`}
