"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowRight, ChevronRight, FileText, MessageSquareText, Search, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { bookDistillApi } from "@/lib/bookdistill-api";
import type { Book, Chapter, Chunk, UploadAccepted } from "@/lib/bookdistill-contracts";
import { Cover, EmptyState, ErrorPanel, LoadingState, StatusBadge } from "./common";
import { BookImages, ObsidianExportPanel } from "./private-assets";

export function LibraryView({books,loading,error,onRetry,onOpen,onAsk,onUpload}:{books:Book[];loading:boolean;error:string|null;onRetry:()=>void;onOpen:(book:Book)=>void;onAsk:(book:Book)=>void;onUpload:()=>void}) {
  const [query,setQuery]=useState("");
  const filtered=useMemo(()=>books.filter((book)=>`${book.title} ${book.authors.join(" ")} ${book.tags.join(" ")}`.toLowerCase().includes(query.toLowerCase())),[books,query]);
  if(loading)return <Page><LoadingState label="正在读取书籍库"/></Page>;
  return <Page>
    <section className="relative overflow-hidden rounded-[28px] bg-[#182238] p-7 text-white sm:p-9"><div className="absolute -right-16 -top-24 size-80 rounded-full border border-white/10"/><div className="relative grid gap-8 lg:grid-cols-[1.15fr_.85fr] lg:items-end"><div><span className="rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs">真实数据链路</span><h2 className="mt-5 max-w-2xl text-3xl font-semibold leading-tight tracking-[-.03em] sm:text-4xl">从书中找答案，回到原文验证。</h2><p className="mt-4 max-w-xl text-base leading-7 text-slate-300">上传、解析、检索、问答与蒸馏全部使用 BookDistill API。</p></div><div className="grid grid-cols-3 gap-3">{[[books.length,"书籍"],[books.filter(b=>b.status==="ready").length,"可问答"],[books.filter(b=>b.status!=="ready"&&b.status!=="failed").length,"处理中"]].map(([value,label])=><div key={String(label)} className="rounded-2xl border border-white/10 bg-white/[.06] p-4"><div className="text-2xl font-semibold">{value}</div><div className="mt-1 text-xs text-slate-400">{label}</div></div>)}</div></div></section>
    {error&&<div className="mt-6"><ErrorPanel message={error} onRetry={onRetry}/></div>}
    <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="relative w-full sm:max-w-md"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"/><input value={query} onChange={e=>setQuery(e.target.value)} className="h-11 w-full rounded-xl border bg-white pl-10 pr-4 text-sm outline-none focus:border-primary" placeholder="搜索书名、作者或标签"/></div><Button onClick={onUpload} className="rounded-xl"><Upload className="size-4"/>上传 PDF</Button></div>
    {!filtered.length?<div className="mt-6"><EmptyState title={books.length?"没有匹配的书籍":"书籍库还是空的"} description="上传 PDF 后，后台任务会依次完成解析、章节识别、切分与索引。" action={<Button onClick={onUpload}><Upload/>上传第一本书</Button>}/></div>:<div className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-3">{filtered.map(book=><article key={book.book_id} className="rounded-2xl border bg-white p-5 shadow-[0_8px_30px_rgb(30_41_59/4%)]"><div className="flex gap-4"><Cover book={book}/><div className="min-w-0 flex-1 py-1"><StatusBadge status={book.status}/><h3 className="mt-3 line-clamp-2 font-semibold leading-6">{book.title}</h3><p className="mt-1 truncate text-sm text-muted-foreground">{book.authors.join("、")||"作者未识别"}</p><p className="mt-3 text-xs text-muted-foreground">{book.page_count||0} 页 · {(book.size_bytes/1024/1024).toFixed(1)} MB</p></div></div><div className="mt-5 flex gap-2 border-t pt-4"><Button onClick={()=>onOpen(book)} variant="outline" className="flex-1 rounded-xl">打开书籍</Button><Button onClick={()=>onAsk(book)} disabled={book.status!=="ready"} className="rounded-xl"><MessageSquareText className="size-4"/>提问</Button></div></article>)}</div>}
  </Page>;
}

export function BookView({book,onAsk,onConfigureKey}:{book:Book;onAsk:()=>void;onConfigureKey:()=>void}) {
  const [chapters,setChapters]=useState<Chapter[]>([]);
  const [chunks,setChunks]=useState<Chunk[]>([]);
  const [chapterId,setChapterId]=useState("");
  const [section,setSection]=useState<"text"|"images">(book.status==="failed"?"images":"text");
  const [error,setError]=useState<string|null>(null);
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError(null);setChapters([]);setChunks([]);setChapterId("");
    Promise.all([bookDistillApi.getChapters(book.book_id,controller.signal),bookDistillApi.getChunks(book.book_id,controller.signal)])
      .then(([values,blocks])=>{if(!controller.signal.aborted){setChapters(values);setChunks(blocks);setChapterId(values[0]?.chapter_id??"")}})
      .catch(cause=>{if(!controller.signal.aborted)setError(cause instanceof Error?cause.message:String(cause))})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[book.book_id,book.updated_at]);
  const visible=chapterId?chunks.filter(chunk=>chunk.chapter_id===chapterId):chunks;
  const selected=chapters.find(chapter=>chapter.chapter_id===chapterId);
  return <div className="min-h-[calc(100vh-72px)] xl:grid xl:grid-cols-[280px_minmax(0,1fr)_270px]">
    <aside className="border-r bg-white p-5">
      <div className="mb-5 flex items-center gap-3"><Cover book={book} className="h-20 w-14"/><div className="min-w-0"><h2 className="line-clamp-2 text-sm font-semibold">{book.title}</h2><p className="mt-1 truncate text-xs text-muted-foreground">{book.authors.join("、")||"作者未识别"}</p></div></div>
      <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">目录 · {chapters.length} 章</p>
      <div className="space-y-1">{chapters.map(chapter=><button key={chapter.chapter_id} onClick={()=>{setChapterId(chapter.chapter_id);setSection("text")}} className={`flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left text-sm ${chapterId===chapter.chapter_id?"bg-secondary text-primary":"hover:bg-muted"}`}><ChevronRight className="mt-0.5 size-4 shrink-0"/><span className="flex-1">{chapter.title}</span><span className="text-xs opacity-60">{chapter.page_start+1}</span></button>)}</div>
    </aside>
    <article className="bg-[#fbfbfc] px-5 py-8 sm:px-10 lg:px-14"><div className="mx-auto max-w-3xl">
      <div className="mb-6 flex gap-2" aria-label="书籍内容"><Button variant={section==="text"?"default":"outline"} aria-pressed={section==="text"} onClick={()=>setSection("text")}>章节原文</Button><Button variant={section==="images"?"default":"outline"} aria-pressed={section==="images"} onClick={()=>setSection("images")}>书中图片</Button></div>
      {section==="images"?<BookImages book={book} onConfigureKey={onConfigureKey}/>:loading?<LoadingState label="正在加载章节与原文块"/>:<>
        {error&&<ErrorPanel message={error}/>}
        <div className="mb-7 flex items-start justify-between gap-3 border-b pb-5"><div><p className="text-xs font-medium text-primary">{selected?`第 ${selected.order_index+1} 章 · 层级 ${selected.level}`:"已切分原文"}</p><h2 className="mt-2 text-2xl font-semibold">{selected?.title??book.title}</h2></div>{selected&&<span className="shrink-0 rounded-lg border bg-white px-3 py-1.5 text-sm text-muted-foreground">PDF {selected.page_start+1}–{selected.page_end+1}</span>}</div>
        {visible.length?<div className="space-y-5">{visible.map(chunk=><section key={chunk.chunk_id} className="chunk-block"><div className="chunk-label">{chunk.content_type} · PDF {chunk.page_start+1}–{chunk.page_end+1} · {chunk.token_count} tokens</div><p className="whitespace-pre-wrap text-[16px] leading-8 text-[#323b4d]">{chunk.text}</p></section>)}</div>:<EmptyState title={book.status==="failed"?"文本处理失败":"此章节暂无文本块"} description={book.status==="failed"?"请在系统状态中查看失败原因。已保存图片可通过“书中图片”查看；扫描页暂不支持文本识别。":"可在系统状态页查看处理任务，或等待索引阶段完成。"} action={<Button variant="outline" onClick={()=>setSection("images")}>查看书中图片</Button>}/>}
      </>}
    </div></article>
    <aside className="border-l bg-white p-5"><Button onClick={onAsk} disabled={book.status!=="ready"} className="w-full rounded-xl"><MessageSquareText className="size-4"/>基于本书提问</Button><div className="mt-7 space-y-4 text-sm"><Metric label="状态" value={book.status}/><Metric label="章节" value={String(chapters.length)}/><Metric label="文本块" value={String(chunks.length)}/><Metric label="语言" value={book.language}/><Metric label="协议" value={book.schema_version}/></div><ObsidianExportPanel book={book} onConfigureKey={onConfigureKey}/></aside>
  </div>;
}

export function UploadDialog({open,onClose,onUploaded}:{open:boolean;onClose:()=>void;onUploaded:(result:UploadAccepted)=>void}) { const [file,setFile]=useState<File|null>(null); const [busy,setBusy]=useState(false); const [error,setError]=useState<string|null>(null); const submit=async()=>{if(!file)return;setBusy(true);setError(null);try{const result=await bookDistillApi.uploadBook(file,{title:file.name.replace(/\.pdf$/i,"")});onUploaded(result);setFile(null);onClose();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false)}}; return <Dialog open={open} onOpenChange={value=>!value&&onClose()}><DialogContent><DialogHeader><DialogTitle>上传 PDF</DialogTitle><DialogDescription>文件会提交到真实摄取接口，并创建可追踪的后台处理任务。</DialogDescription></DialogHeader>{error&&<ErrorPanel message={error}/>}<label className="my-3 flex cursor-pointer flex-col items-center rounded-2xl border border-dashed border-primary/35 bg-secondary/25 px-6 py-10 text-center"><input type="file" accept="application/pdf,.pdf" className="sr-only" onChange={e=>setFile(e.target.files?.[0]??null)}/><span className="grid size-12 place-items-center rounded-2xl bg-white text-primary"><FileText/></span><strong className="mt-4 text-sm">{file?.name??"选择一个 PDF 文件"}</strong><span className="mt-1 text-xs text-muted-foreground">最大容量由后端 BOOKDISTILL_MAX_UPLOAD_BYTES 控制</span></label><DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button onClick={()=>void submit()} disabled={!file||busy}>{busy?"正在提交…":"确认上传"}<ArrowRight className="size-4"/></Button></DialogFooter></DialogContent></Dialog>; }

function Page({children}:{children:ReactNode}){return <div className="mx-auto max-w-[1320px] px-5 py-8 sm:px-8 lg:px-10">{children}</div>}
function Metric({label,value}:{label:string;value:string}){return <div className="flex justify-between border-b pb-3"><span className="text-muted-foreground">{label}</span><span className="font-medium">{value}</span></div>}
