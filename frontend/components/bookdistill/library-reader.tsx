"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRight, ChevronRight, FileText, MessageSquareText, Search, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { bookDistillApi } from "@/lib/bookdistill-api";
import type { Book, Chapter, Chunk, TextBook } from "@/lib/bookdistill-contracts";
import { Cover, EmptyState, ErrorPanel, LoadingState, StatusBadge } from "./common";
import { BookImages, ObsidianExportPanel } from "./private-assets";

export type LibraryBook = {kind:"pdf";book:Book}|{kind:"text";book:TextBook};

export function LibraryView({books,textBooks,loading,error,onRetry,onOpen,onAsk,onUpload}:{books:Book[];textBooks:TextBook[];loading:boolean;error:string|null;onRetry:()=>void;onOpen:(item:LibraryBook)=>void;onAsk:(item:LibraryBook)=>void;onUpload:()=>void}) {
  const [query,setQuery]=useState("");
  const allBooks=useMemo<LibraryBook[]>(()=>[...books.map(book=>({kind:"pdf" as const,book})),...textBooks.map(book=>({kind:"text" as const,book}))],[books,textBooks]);
  const filtered=useMemo(()=>allBooks.filter(item=>`${item.book.title} ${item.kind==="pdf"?item.book.authors.join(" ")+" "+item.book.tags.join(" "):item.book.format}`.toLowerCase().includes(query.toLowerCase())),[allBooks,query]);
  if(loading)return <Page><LoadingState label="正在读取书籍库"/></Page>;
  return <Page>
    <section className="relative overflow-hidden rounded-[28px] bg-[#182238] p-7 text-white sm:p-9"><div className="absolute -right-16 -top-24 size-80 rounded-full border border-white/10"/><div className="relative grid gap-8 lg:grid-cols-[1.15fr_.85fr] lg:items-end"><div><span className="rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs">真实数据链路</span><h2 className="mt-5 max-w-2xl text-3xl font-semibold leading-tight tracking-[-.03em] sm:text-4xl">从书中找答案，回到原文验证。</h2><p className="mt-4 max-w-xl text-base leading-7 text-slate-300">上传、解析、检索、问答与蒸馏全部使用 BookDistill API。</p></div><div className="grid grid-cols-3 gap-3">{[[allBooks.length,"书籍"],[books.filter(b=>b.status==="ready").length+textBooks.filter(b=>b.retrieval_ready).length,"可检索"],[books.filter(b=>b.status!=="ready"&&b.status!=="failed").length+textBooks.filter(b=>b.status==="queued"||b.status==="processing").length,"处理中"]].map(([value,label])=><div key={String(label)} className="rounded-2xl border border-white/10 bg-white/[.06] p-4"><div className="text-2xl font-semibold">{value}</div><div className="mt-1 text-xs text-slate-400">{label}</div></div>)}</div></div></section>
    {error&&<div className="mt-6"><ErrorPanel message={error} onRetry={onRetry}/></div>}
    <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="relative w-full sm:max-w-md"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"/><input value={query} onChange={e=>setQuery(e.target.value)} className="h-11 w-full rounded-xl border bg-white pl-10 pr-4 text-sm outline-none focus:border-primary" placeholder="搜索书名、作者或标签"/></div><Button onClick={onUpload} className="rounded-xl"><Upload className="size-4"/>上传书籍</Button></div>
    {!filtered.length?<div className="mt-6"><EmptyState title={allBooks.length?"没有匹配的书籍":"书籍库还是空的"} description="上传书籍后，后台任务会完成解析、章节识别、切分与索引。" action={<Button onClick={onUpload}><Upload/>上传第一本书</Button>}/></div>:<div className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-3">{filtered.map(item=><LibraryCard key={`${item.kind}:${item.book.book_id}`} item={item} onOpen={onOpen} onAsk={onAsk}/>)}</div>}
  </Page>;
}

function LibraryCard({item,onOpen,onAsk}:{item:LibraryBook;onOpen:(item:LibraryBook)=>void;onAsk:(item:LibraryBook)=>void}) {
  const book=item.book;
  const pdfBook=item.kind==="pdf"?item.book:null;
  const textBook=item.kind==="text"?item.book:null;
  const ready=pdfBook?.status==="ready"||textBook?.retrieval_ready===true;
  return <article className="rounded-2xl border bg-white p-5 shadow-[0_8px_30px_rgb(30_41_59/4%)]">
    <div className="flex gap-4"><Cover book={book}/><div className="min-w-0 flex-1 py-1"><StatusBadge status={ready?"ready":book.status}/><h3 className="mt-3 line-clamp-2 font-semibold leading-6">{book.title}</h3><p className="mt-1 truncate text-sm text-muted-foreground">{book.authors?.join("、")||"作者未识别"}</p><p className="mt-1 text-xs font-medium text-muted-foreground">文件格式：{pdfBook?"PDF":textBook?.format.toUpperCase()}</p><p className="mt-3 text-xs text-muted-foreground">{pdfBook?`${pdfBook.page_count||0} 页`:textBook?.source_unit_count==null?"正在解析正文":`${textBook.source_unit_count} 个原文单元`} · {((book.size_bytes??0)/1024/1024).toFixed(1)} MB</p></div></div>
    <div className="mt-5 flex gap-2 border-t pt-4"><Button onClick={()=>onOpen(item)} variant="outline" className="flex-1 rounded-xl">打开书籍</Button><Button onClick={()=>onAsk(item)} disabled={!ready} className="rounded-xl"><MessageSquareText className="size-4"/>{pdfBook?"提问":"检索"}</Button></div>
  </article>;
}

export function BookView({book,onAsk,onConfigureKey,focusChunkId}:{book:Book;onAsk:()=>void;onConfigureKey:()=>void;focusChunkId?:string}) {
  const [snapshot,setSnapshot]=useState<{identity:string;chapters:Chapter[];chunks:Chunk[];error:string|null}|null>(null);
  const [chapterId,setChapterId]=useState("");
  const [section,setSection]=useState<"text"|"images">(book.status==="failed"?"images":"text");
  const readingPane=useRef<HTMLElement>(null);
  const identity=`${book.book_id}:${book.updated_at}`;
  const current=snapshot?.identity===identity?snapshot:null;
  const chapters=current?.chapters??[];
  const chunks=current?.chunks??[];
  const loading=current===null;
  const error=current?.error??null;
  useEffect(()=>{
    if(!current||!focusChunkId)return;
    const match=current.chunks.find(chunk=>chunk.chunk_id===focusChunkId);
    if(!match)return;
    const selectTimer=window.setTimeout(()=>{setChapterId(match.chapter_id);setSection("text")},0);
    const scrollTimer=window.setTimeout(()=>readingPane.current?.querySelector(`[data-chunk-id="${focusChunkId}"]`)?.scrollIntoView({behavior:"smooth",block:"center"}),150);
    return()=>{window.clearTimeout(selectTimer);window.clearTimeout(scrollTimer)};
  },[current,focusChunkId]);
  useEffect(()=>{
    const controller=new AbortController();
    Promise.all([bookDistillApi.getChapters(book.book_id,controller.signal),bookDistillApi.getChunks(book.book_id,controller.signal)])
      .then(([values,blocks])=>{if(!controller.signal.aborted)setSnapshot({identity,chapters:values,chunks:blocks,error:null})})
      .catch(cause=>{if(!controller.signal.aborted)setSnapshot({identity,chapters:[],chunks:[],error:cause instanceof Error?cause.message:String(cause)})});
    return()=>controller.abort();
  },[book.book_id,identity]);
  const selected=chapters.find(chapter=>chapter.chapter_id===chapterId)??chapters[0];
  const chapterById=new Map(chapters.map(chapter=>[chapter.chapter_id,chapter]));
  const visible=selected?chunks.filter(chunk=>{
    let chapter=chapterById.get(chunk.chapter_id);
    const visited=new Set<string>();
    while(chapter&&!visited.has(chapter.chapter_id)){
      visited.add(chapter.chapter_id);
      if(chapter.chapter_id===selected.chapter_id)return true;
      chapter=chapter.parent_id?chapterById.get(chapter.parent_id):undefined;
    }
    return false;
  }):chunks;
  const selectChapter=(id:string)=>{setChapterId(id);setSection("text");readingPane.current?.scrollTo({top:0,behavior:"instant"});if(!window.matchMedia("(min-width: 1280px)").matches)readingPane.current?.scrollIntoView({behavior:"instant",block:"start"})};
  return <div className="min-h-[calc(100vh-72px)] xl:grid xl:h-[calc(100dvh-72px)] xl:grid-cols-[280px_minmax(0,1fr)_270px] xl:overflow-hidden">
    <aside className="max-h-[40dvh] overflow-y-auto overscroll-contain border-r bg-white p-5 xl:max-h-none xl:min-h-0">
      <div className="mb-5 flex items-center gap-3"><Cover book={book} className="h-20 w-14"/><div className="min-w-0"><h2 className="line-clamp-2 text-sm font-semibold">{book.title}</h2><p className="mt-1 truncate text-xs text-muted-foreground">{book.authors.join("、")||"作者未识别"}</p><p className="mt-1 text-xs text-muted-foreground">文件格式：PDF</p></div></div>
      <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">目录 · {chapters.length} 章</p>
      <div className="space-y-1">{chapters.map(chapter=><button key={chapter.chapter_id} onClick={()=>selectChapter(chapter.chapter_id)} style={{paddingLeft:`${12+Math.min(chapter.level-1,4)*16}px`}} className={`flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left text-sm ${selected?.chapter_id===chapter.chapter_id?"bg-secondary text-primary":"hover:bg-muted"}`}><ChevronRight className="mt-0.5 size-4 shrink-0"/><span className="min-w-0 flex-1"><span className="block break-words">{chapter.title}</span><span className="mt-1 block text-xs opacity-60">第 {chapter.page_start+1} 页</span></span></button>)}</div>
    </aside>
    <article ref={readingPane} className="scroll-mt-[72px] bg-[#fbfbfc] px-5 py-8 sm:px-10 lg:px-14 xl:min-h-0 xl:overflow-y-auto xl:overscroll-contain"><div className="mx-auto max-w-3xl">
      <div className="mb-6 flex gap-2" aria-label="书籍内容"><Button variant={section==="text"?"default":"outline"} aria-pressed={section==="text"} onClick={()=>setSection("text")}>章节原文</Button><Button variant={section==="images"?"default":"outline"} aria-pressed={section==="images"} onClick={()=>setSection("images")}>书中图片</Button></div>
      {section==="images"?<BookImages book={book} onConfigureKey={onConfigureKey}/>:loading?<LoadingState label="正在加载章节与原文块"/>:<>
        {error&&<ErrorPanel message={error}/>}
        <div className="mb-7 flex items-start justify-between gap-3 border-b pb-5"><div><p className="text-xs font-medium text-primary">{selected?`第 ${selected.order_index+1} 章 · 层级 ${selected.level}`:"已切分原文"}</p><h2 className="mt-2 text-2xl font-semibold">{selected?.title??book.title}</h2></div>{selected&&<span className="shrink-0 rounded-lg border bg-white px-3 py-1.5 text-sm text-muted-foreground">PDF {selected.page_start+1}–{selected.page_end+1}</span>}</div>
        {visible.length?<div className="space-y-5">{visible.map(chunk=><section key={chunk.chunk_id} data-chunk-id={chunk.chunk_id} className={`chunk-block ${focusChunkId===chunk.chunk_id?"ring-2 ring-primary":""}`}><div className="chunk-label">{chunk.content_type} · PDF {chunk.page_start+1}–{chunk.page_end+1} · {chunk.token_count} tokens</div><p className="whitespace-pre-wrap text-[16px] leading-8 text-[#323b4d]">{chunk.text}</p></section>)}</div>:<EmptyState title={book.status==="failed"?"文本处理失败":"此章节暂无文本块"} description={book.status==="failed"?"请在系统状态中查看失败原因。已保存图片可通过“书中图片”查看；扫描页暂不支持文本识别。":"可在系统状态页查看处理任务，或等待索引阶段完成。"} action={<Button variant="outline" onClick={()=>setSection("images")}>查看书中图片</Button>}/>}
      </>}
    </div></article>
    <aside className="border-l bg-white p-5 xl:min-h-0 xl:overflow-y-auto"><Button onClick={onAsk} disabled={book.status!=="ready"} className="w-full rounded-xl"><MessageSquareText className="size-4"/>基于本书提问</Button><div className="mt-7 space-y-4 text-sm"><Metric label="状态" value={book.status}/><Metric label="章节" value={String(chapters.length)}/><Metric label="文本块" value={String(chunks.length)}/><Metric label="语言" value={book.language}/><Metric label="协议" value={book.schema_version}/></div><ObsidianExportPanel book={book} onConfigureKey={onConfigureKey}/></aside>
  </div>;
}

const supportedBookTypes = [
  {name:"PDF",extension:".pdf",detail:"按页定位"},
  {name:"纯文本",extension:".txt",detail:"UTF-8，按行定位"},
  {name:"Markdown",extension:".md / .markdown",detail:"保留标题层级"},
  {name:"EPUB",extension:".epub",detail:"按章节和锚点定位"},
  {name:"Word",extension:".docx",detail:"保留段落与表格顺序"},
];

export function UploadDialog({open,onClose,onUploaded}:{open:boolean;onClose:()=>void;onUploaded:(kind:"pdf"|"text")=>void}) {
  const [file,setFile]=useState<File|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const close=()=>{setFile(null);setError(null);onClose()};
  const submit=async()=>{
    if(!file)return;
    const extension=file.name.split(".").pop()?.toLowerCase();
    if(!extension||!["pdf","txt","md","markdown","epub","docx"].includes(extension)){
      setError("请选择下列支持的书籍文件类型。");
      return;
    }
    setBusy(true);setError(null);
    try{
      const title=file.name.replace(/\.(pdf|txt|md|markdown|epub|docx)$/i,"");
      if(extension==="pdf"){
        await bookDistillApi.uploadBook(file,{title});
        onUploaded("pdf");
      }else{
        await bookDistillApi.uploadTextBook(file,{title});
        onUploaded("text");
      }
      close();
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setBusy(false)}
  };
  return <Dialog open={open} onOpenChange={value=>!value&&!busy&&close()}><DialogContent>
    <DialogHeader><DialogTitle>上传书籍</DialogTitle><DialogDescription>选择文件后会创建可追踪的后台处理任务。</DialogDescription></DialogHeader>
    <div className="rounded-xl border bg-muted/35 p-4"><p className="text-sm font-medium">支持的文档类型</p><ul className="mt-3 grid gap-2 sm:grid-cols-2">{supportedBookTypes.map(type=><li key={type.name} className="rounded-lg border bg-white px-3 py-2 text-sm"><span className="font-medium">{type.name}</span><span className="ml-2 text-xs text-muted-foreground">{type.extension}</span><p className="mt-1 text-xs text-muted-foreground">{type.detail}</p></li>)}</ul></div>
    {error&&<ErrorPanel message={error}/>}
    <label className="my-2 flex cursor-pointer flex-col items-center rounded-2xl border border-dashed border-primary/35 bg-secondary/25 px-6 py-8 text-center"><input type="file" accept=".pdf,.txt,.md,.markdown,.epub,.docx,application/pdf,text/plain,text/markdown,application/epub+zip,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="sr-only" onChange={event=>{setFile(event.target.files?.[0]??null);setError(null)}}/><span className="grid size-12 place-items-center rounded-2xl bg-white text-primary"><FileText/></span><strong className="mt-4 text-sm">{file?.name??"选择书籍文件"}</strong><span className="mt-1 text-xs text-muted-foreground">最大容量由后端 BOOKDISTILL_MAX_UPLOAD_BYTES 控制</span></label>
    <DialogFooter><Button variant="outline" onClick={close} disabled={busy}>取消</Button><Button onClick={()=>void submit()} disabled={!file||busy}>{busy?"正在提交…":"确认上传"}<ArrowRight className="size-4"/></Button></DialogFooter>
  </DialogContent></Dialog>;
}

function Page({children}:{children:ReactNode}){return <div className="mx-auto max-w-[1320px] px-5 py-8 sm:px-8 lg:px-10">{children}</div>}
function Metric({label,value}:{label:string;value:string}){return <div className="flex justify-between border-b pb-3"><span className="text-muted-foreground">{label}</span><span className="font-medium">{value}</span></div>}
