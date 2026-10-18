"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, BookOpenCheck, LoaderCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { bookDistillApi } from "@/lib/bookdistill-api";
import type { AgentRunView, Book, Citation } from "@/lib/bookdistill-contracts";

const terminal = new Set(["succeeded", "refused", "failed"]);
const phaseLabel: Record<string,string> = {searching:"逐书检索证据",comparing:"比较书籍观点",questions:"生成复习题",done:"研究完成"};

export function CrossBookResearch({books,onOpenCitation}:{books:Book[];onOpenCitation:(citation:Citation)=>void}) {
  const available=useMemo(()=>books.filter(book=>book.status==="ready"),[books]);
  const [selected,setSelected]=useState<string[]>([]);
  const [question,setQuestion]=useState("");
  const [questionCount,setQuestionCount]=useState(3);
  const [run,setRun]=useState<AgentRunView|null>(null);
  const [submitting,setSubmitting]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const key=useRef<string|null>(null);

  useEffect(()=>{
    const runId=window.sessionStorage.getItem("bookdistill.researchRunId");
    if(!runId)return;
    let active=true;
    void bookDistillApi.getResearchRun(runId).then(value=>{if(active)setRun(value)}).catch(()=>{window.sessionStorage.removeItem("bookdistill.researchRunId")});
    return()=>{active=false};
  },[]);

  useEffect(()=>{
    if(!run||terminal.has(run.status))return;
    let active=true;
    const timer=window.setInterval(()=>{
      void bookDistillApi.getResearchRun(run.run_id).then(value=>{if(active)setRun(value)}).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:String(cause))});
    },1800);
    return()=>{active=false;window.clearInterval(timer)};
  },[run]);

  const toggle=(bookId:string)=>{key.current=null;setSelected(current=>current.includes(bookId)?current.filter(id=>id!==bookId):current.length<10?[...current,bookId]:current)};
  const start=async()=>{
    if(selected.length<2||!question.trim())return;
    setSubmitting(true);setError(null);setRun(null);
    key.current??=crypto.randomUUID();
    try{const created=await bookDistillApi.createResearchRun(question.trim(),selected,questionCount,key.current);setRun(created);window.sessionStorage.setItem("bookdistill.researchRunId",created.run_id)}
    catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setSubmitting(false)}
  };
  const citations=run?.result?.comparison?.citations??[];
  const titleFor=(id:string)=>available.find(book=>book.book_id===id)?.title??id;
  return <div className="mx-auto max-w-6xl space-y-7 px-5 py-8 sm:px-8 lg:px-10">
    <div className="rounded-3xl border bg-gradient-to-br from-[#f4f8f9] to-white p-6 sm:p-8">
      <div className="flex items-center gap-3 text-primary"><BookOpenCheck className="size-6"/><span className="text-sm font-semibold tracking-wide">跨书研究</span></div>
      <h2 className="mt-4 text-2xl font-semibold sm:text-3xl">让多本书围绕同一个问题对话</h2>
      <p className="mt-3 max-w-2xl text-sm leading-7 text-muted-foreground">选择至少两本已完成索引的 PDF，查看逐书观点、原页证据和基于同一批证据生成的复习题。引用由 BookDistill 校验。</p>
    </div>
    <section className="rounded-2xl border bg-white p-5 sm:p-7">
      <div className="flex items-center justify-between gap-3"><h3 className="font-semibold">1. 选择书籍</h3><span className="text-xs text-muted-foreground">已选 {selected.length}/10 本</span></div>
      {available.length<2?<p className="mt-4 text-sm text-muted-foreground">需要至少两本状态为“已就绪”的 PDF 书籍。先在书籍库完成上传与索引。</p>:<div className="mt-4 grid gap-3 sm:grid-cols-2">{available.map(book=><label key={book.book_id} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 ${selected.includes(book.book_id)?"border-primary bg-primary/5":"hover:bg-muted/40"}`}><input type="checkbox" checked={selected.includes(book.book_id)} onChange={()=>toggle(book.book_id)} className="mt-1 accent-primary"/><span><strong className="block text-sm">{book.title}</strong><small className="mt-1 block text-muted-foreground">{book.authors.join("、")||"作者未识别"} · {book.page_count} 页</small></span></label>)}</div>}
      <label className="mt-6 block text-sm font-semibold" htmlFor="research-question">2. 提出比较问题</label>
      <textarea id="research-question" value={question} maxLength={4000} onChange={event=>{setQuestion(event.target.value);key.current=null}} placeholder="例如：两本书如何解释 Agent 的规划与工具使用？" className="mt-3 min-h-28 w-full rounded-xl border bg-background p-4 text-sm outline-none focus:border-primary"/>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-4"><label className="text-sm">复习题数量 <select value={questionCount} onChange={event=>{setQuestionCount(Number(event.target.value));key.current=null}} className="ml-2 rounded-lg border bg-background px-2 py-1">{[1,2,3,4,5].map(value=><option key={value} value={value}>{value}</option>)}</select></label><Button onClick={()=>void start()} disabled={submitting||selected.length<2||!question.trim()}>{submitting?<LoaderCircle className="size-4 animate-spin"/>:<ArrowRight className="size-4"/>}开始研究</Button></div>
    </section>
    {error&&<div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
    {run&&<section className="space-y-6"><div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-white p-5"><div><p className="text-sm font-semibold">{run.status==="succeeded"?"研究完成":run.status==="refused"?"证据不足，已停止生成":run.status==="failed"?"研究失败":phaseLabel[run.phase]??"处理中"}</p><p className="mt-1 text-xs text-muted-foreground">运行 ID：{run.run_id} · 状态：{run.status}</p></div>{!terminal.has(run.status)&&<RefreshCw className="size-5 animate-spin text-primary"/>}</div>
      {run.status==="failed"&&<p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm">错误代码：{run.error_code??"unknown"}。请检查 Agent 服务及上游任务状态。</p>}
      {run.status==="refused"&&<p className="rounded-xl border bg-amber-50 p-4 text-sm">以下书籍未找到足够证据：{(run.result?.missing_book_ids??[]).map(titleFor).join("、")}。请换一个更具体的问题。</p>}
      {run.result?.comparison&&<div className="space-y-5"><h3 className="text-xl font-semibold">逐书观点</h3><div className="grid gap-4 md:grid-cols-2">{run.result.comparison.per_book.map(item=><article key={item.book_id} className="rounded-2xl border bg-white p-5"><h4 className="font-semibold">{item.book_title}</h4><p className="mt-3 whitespace-pre-wrap text-sm leading-7">{item.viewpoint}</p><p className="mt-3 text-xs text-muted-foreground">证据 {item.evidence_ids.length} 条</p></article>)}</div>{(["commonalities","differences","applicability"] as const).map((section,index)=>{const items=run.result?.comparison?.[section]??[];return items.length?<div key={section} className="rounded-2xl border bg-white p-5"><h4 className="font-semibold">{["共同点","差异","适用场景"][index]}</h4><ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-7">{items.map((item,i)=><li key={i}>{item.text}</li>)}</ul></div>:null})}</div>}
      {citations.length>0&&<div><h3 className="text-xl font-semibold">来源与原页</h3><div className="mt-4 grid gap-3 sm:grid-cols-2">{citations.map(citation=><button key={citation.evidence_id} onClick={()=>onOpenCitation(citation)} className="rounded-xl border bg-white p-4 text-left hover:border-primary hover:bg-primary/5"><span className="text-sm font-semibold">{citation.book_title??titleFor(citation.book_id)}</span><span className="ml-2 text-xs text-primary">PDF 第 {citation.page_start+1}–{citation.page_end+1} 页</span><p className="mt-2 line-clamp-3 text-sm leading-6 text-muted-foreground">{citation.quote}</p><span className="mt-3 inline-block text-xs text-primary">打开原文位置 →</span></button>)}</div></div>}
      {run.result?.questions&&<div><h3 className="text-xl font-semibold">基于同一证据的复习题</h3><div className="mt-4 space-y-3">{run.result.questions.questions.map((item,index)=><details key={index} className="rounded-xl border bg-white p-5"><summary className="cursor-pointer font-medium">{index+1}. {item.stem}</summary><div className="mt-4 space-y-2 text-sm leading-7"><p><b>答案：</b>{item.answer}</p><p><b>解析：</b>{item.explanation}</p><div className="flex flex-wrap gap-2">{run.result?.questions?.citations.filter(citation=>item.evidence_ids.includes(citation.evidence_id)).map(citation=><button key={citation.evidence_id} onClick={()=>onOpenCitation(citation)} className="rounded-lg border px-2 py-1 text-xs text-primary hover:bg-primary/5">{citation.book_title??titleFor(citation.book_id)} · PDF 第 {citation.page_start+1} 页</button>)}</div></div></details>)}</div></div>}
    </section>}
  </div>;
}
