"use client";

import { useEffect, useRef, useState } from "react";
import { Download, ImagePlus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { bookDistillApi, CONNECTION_CHANGE_EVENT, privateContentError } from "@/lib/bookdistill-api";
import type { Book, ImageAssetList, ImageAssetView } from "@/lib/bookdistill-contracts";
import { EmptyState, ErrorPanel, LoadingState } from "./common";

const PAGE_SIZE=24;
const UUID_PATTERN=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const imageFilename=(image:ImageAssetView)=>`${image.asset_id}.${{"image/png":"png","image/jpeg":"jpg","image/webp":"webp"}[image.mime_type]}`;

function usePrivateRevision(){
  const [revision,setRevision]=useState(0);
  useEffect(()=>{const refresh=()=>setRevision(value=>value+1);window.addEventListener(CONNECTION_CHANGE_EVENT,refresh);return()=>window.removeEventListener(CONNECTION_CHANGE_EVENT,refresh)},[]);
  return [revision,()=>setRevision(value=>value+1)] as const;
}

function useDownload(){
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [started,setStarted]=useState(false);
  const request=useRef<AbortController|null>(null);
  const urls=useRef(new Map<string,number>());
  useEffect(()=>{
    const ownedUrls=urls.current;
    const release=()=>{request.current?.abort();request.current=null;for(const [url,timer] of ownedUrls){window.clearTimeout(timer);URL.revokeObjectURL(url)}ownedUrls.clear()};
    const changed=()=>{release();setBusy(false);setError(null);setStarted(false)};
    window.addEventListener(CONNECTION_CHANGE_EVENT,changed);
    window.addEventListener("pagehide",changed);
    return()=>{release();window.removeEventListener(CONNECTION_CHANGE_EVENT,changed);window.removeEventListener("pagehide",changed)};
  },[]);
  const run=async(load:(signal:AbortSignal)=>Promise<Blob>,filename:string)=>{
    if(request.current)return;
    const controller=new AbortController();request.current=controller;
    setBusy(true);setError(null);setStarted(false);
    try{
      const blob=await load(controller.signal);
      if(controller.signal.aborted)return;
      const url=URL.createObjectURL(blob);
      const link=document.createElement("a");link.href=url;link.download=filename;
      const timer=window.setTimeout(()=>{URL.revokeObjectURL(url);urls.current.delete(url)},1000);
      urls.current.set(url,timer);
      document.body.appendChild(link);
      try{link.click();setStarted(true)}finally{link.remove()}
    }catch(cause){if(!controller.signal.aborted)setError(privateContentError(cause))}
    finally{if(request.current===controller){request.current=null;setBusy(false)}}
  };
  return {busy,error,started,run};
}

function AccessHelp({onConfigureKey}:{onConfigureKey:()=>void}){
  return <Button size="sm" variant="outline" onClick={onConfigureKey}>配置访问 Key</Button>;
}

function PrivateImage({image,revision,onConfigureKey,large=false}:{image:ImageAssetView;revision:number;onConfigureKey:()=>void;large?:boolean}){
  const container=useRef<HTMLDivElement>(null);
  const [visible,setVisible]=useState(large);
  const [attempt,setAttempt]=useState(0);
  const [content,setContent]=useState<{identity:string;url:string}|null>(null);
  const [error,setError]=useState<string|null>(null);
  const identity=`${image.asset_id}:${revision}:${attempt}`;
  useEffect(()=>{
    const element=container.current;
    if(large||!element)return;
    if(typeof IntersectionObserver==="undefined"){setVisible(true);return}
    const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setVisible(true);observer.disconnect()}},{rootMargin:"200px"});
    observer.observe(element);return()=>observer.disconnect();
  },[large,image.asset_id]);
  useEffect(()=>{
    if(!visible)return;
    const controller=new AbortController();let objectUrl:string|null=null;
    setError(null);
    bookDistillApi.imageContent(image.asset_id,controller.signal).then(blob=>{
      if(controller.signal.aborted)return;
      objectUrl=URL.createObjectURL(blob);setContent({identity,url:objectUrl});
    }).catch(cause=>{if(!controller.signal.aborted)setError(privateContentError(cause))});
    return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)};
  },[visible,identity,image.asset_id]);
  const url=content?.identity===identity?content.url:null;
  return <div ref={container} className={`grid place-items-center overflow-hidden rounded-xl bg-muted/40 ${large?"min-h-56":"h-40"}`}>
    {error?<div role="alert" className="space-y-2 p-3 text-center text-xs text-rose-700"><p>{error}</p><Button size="sm" variant="outline" onClick={()=>setAttempt(value=>value+1)}>重试图片</Button><AccessHelp onConfigureKey={onConfigureKey}/></div>:url?
      // Authenticated content is a local Blob URL, so the native image element owns its lifecycle.
      // eslint-disable-next-line @next/next/no-img-element
      <img src={url} alt={image.source_type==="pdf"?`PDF 第 ${image.page_index+1} 页的图片`:`上传图片 ${image.asset_id}`} className={large?"max-h-[60vh] max-w-full object-contain":"h-full w-full object-contain"} onError={()=>setError("图片无法显示，请重试或下载原图查看。")}/>
      :<span className="p-3 text-xs text-muted-foreground">{visible?"正在读取图片…":"图片待加载"}</span>}
  </div>;
}

function ImageGallery({images,revision,onConfigureKey}:{images:ImageAssetView[];revision:number;onConfigureKey:()=>void}){
  const [selected,setSelected]=useState<ImageAssetView|null>(null);
  useEffect(()=>setSelected(null),[revision,images]);
  return <>
    <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">{images.map(image=><section key={image.asset_id} className="rounded-2xl border bg-white p-3">
      <PrivateImage image={image} revision={revision} onConfigureKey={onConfigureKey}/>
      <p className="mt-3 text-sm font-medium">{image.source_type==="pdf"?`PDF 第 ${image.page_index+1} 页`:"独立上传图片"}</p>
      <p className="mt-1 text-xs text-muted-foreground">{image.width} × {image.height} · {(image.size_bytes/1024).toFixed(1)} KB</p>
      <p className="mt-1 break-all text-[11px] text-muted-foreground">{image.asset_id}</p>
      <Button variant="outline" size="sm" className="mt-3 w-full" onClick={()=>setSelected(image)}>预览与下载</Button>
    </section>)}</div>
    <Dialog open={!!selected} onOpenChange={open=>!open&&setSelected(null)}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader><DialogTitle>图片预览</DialogTitle><DialogDescription>{selected?`${selected.width} × ${selected.height} · ${selected.mime_type}`:""}</DialogDescription></DialogHeader>
      {selected&&<ImagePreview key={`${selected.asset_id}:${revision}`} image={selected} revision={revision} onConfigureKey={onConfigureKey}/>}
    </DialogContent></Dialog>
  </>;
}

function ImagePreview({image,revision,onConfigureKey}:{image:ImageAssetView;revision:number;onConfigureKey:()=>void}){
  const download=useDownload();
  return <><PrivateImage image={image} revision={revision} large onConfigureKey={onConfigureKey}/>
    <p className="break-all text-xs text-muted-foreground">{image.asset_id}{image.source_type==="pdf"?` · PDF 第 ${image.page_index+1} 页`:""}</p>
    {download.error&&<ErrorPanel message={download.error}/>}
    <Button disabled={download.busy} onClick={()=>void download.run(signal=>bookDistillApi.imageContent(image.asset_id,signal),imageFilename(image))}><Download className="size-4"/>{download.busy?"正在下载…":"下载原图"}</Button>
    {download.started&&<p role="status" className="text-sm text-emerald-700">已开始下载，请查看浏览器下载列表。</p>}
  </>;
}

function Pagination({offset,total,onChange,disabled=false}:{offset:number;total:number;onChange:(offset:number)=>void;disabled?:boolean}){
  return <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm"><span className="text-muted-foreground">{total?`${offset+1}–${Math.min(offset+PAGE_SIZE,total)} / ${total}`:"0 张图片"}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={disabled||offset===0} onClick={()=>onChange(Math.max(0,offset-PAGE_SIZE))}>上一页</Button><Button size="sm" variant="outline" disabled={disabled||offset+PAGE_SIZE>=total} onClick={()=>onChange(offset+PAGE_SIZE)}>下一页</Button></div></div>;
}

export function BookImages({book,onConfigureKey}:{book:Book;onConfigureKey:()=>void}){
  const [images,setImages]=useState<ImageAssetView[]>([]);
  const [offset,setOffset]=useState(0);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<string|null>(null);
  const [revision,refresh]=usePrivateRevision();
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError(null);
    bookDistillApi.getBookImages(book.book_id,controller.signal).then(values=>{if(!controller.signal.aborted){setImages(values);setOffset(0)}})
      .catch(cause=>{if(!controller.signal.aborted){setImages([]);setError(privateContentError(cause))}})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[book.book_id,book.updated_at,revision]);
  return <section className="mt-8 border-t pt-6"><div className="mb-4 flex items-center justify-between gap-3"><h3 className="text-lg font-semibold">书中图片</h3><Button size="sm" variant="outline" onClick={refresh} disabled={loading}><RefreshCw className="size-4"/>刷新</Button></div>
    {book.status==="failed"&&<p className="mb-4 rounded-xl bg-amber-50 p-4 text-sm leading-6 text-amber-900">本书文本处理失败。扫描页暂不支持文本识别；处理失败的具体原因请在系统状态中查看。已提取并保存的图片仍可预览和下载。</p>}
    {loading?<LoadingState label="正在读取图片清单"/>:error?<div className="space-y-3"><ErrorPanel message={error} onRetry={refresh}/><AccessHelp onConfigureKey={onConfigureKey}/></div>:images.length?<><ImageGallery images={images.slice(offset,offset+PAGE_SIZE)} revision={revision} onConfigureKey={onConfigureKey}/><Pagination offset={offset} total={images.length} onChange={setOffset}/></>:<EmptyState title="暂无已保存图片" description="处理完成后会显示 PDF 中的嵌入位图。纯文字或矢量图页面可能没有图片。"/>}
  </section>;
}

export function ObsidianExportPanel({book,onConfigureKey}:{book:Book;onConfigureKey:()=>void}){
  const [includePdf,setIncludePdf]=useState(false);
  const [artifactText,setArtifactText]=useState("");
  const [validation,setValidation]=useState<string|null>(null);
  const download=useDownload();
  const exportZip=()=>{
    const ids=[...new Set(artifactText.split(/[\s,，;；]+/).filter(Boolean).map(id=>id.toLowerCase()))];
    if(ids.some(id=>!UUID_PATTERN.test(id))){setValidation("产物 ID 应为完整 UUID，请用换行或逗号分隔。");return}
    if(ids.length>100){setValidation("每次最多选择 100 个不同的产物 ID。");return}
    setValidation(null);
    void download.run(signal=>bookDistillApi.exportObsidian(book.book_id,{includePdf,artifactIds:ids},signal),`bookdistill-${book.book_id}.zip`);
  };
  return <section className="mt-7 space-y-3 border-t pt-5"><h3 className="font-semibold">导出到 Obsidian</h3><p className="text-xs leading-5 text-muted-foreground">下载章节、图片和所选保存产物。解压到 vault 根目录后即可阅读。</p>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={includePdf} disabled={download.busy} onChange={event=>setIncludePdf(event.target.checked)}/>包含原 PDF 与页链接</label>
    <label className="block text-xs font-medium">保存产物 ID（可选）<textarea value={artifactText} disabled={download.busy} onChange={event=>{setArtifactText(event.target.value);setValidation(null)}} rows={3} placeholder="从蒸馏结果复制 ID；换行或逗号分隔" className="field mt-2 h-auto w-full py-2 text-xs"/></label>
    <p className="text-xs leading-5 text-muted-foreground">最多 100 个 ID。留空只导出本书；导出所选保存版本，并保留跨书引用。导出不会调用模型。</p>
    {(validation||download.error)&&<div role="alert"><ErrorPanel message={validation??download.error??""}/><div className="mt-2"><AccessHelp onConfigureKey={onConfigureKey}/></div></div>}
    <Button className="w-full" disabled={download.busy} onClick={exportZip}><Download className="size-4"/>{download.busy?"正在生成 ZIP…":"下载 Obsidian ZIP"}</Button>
    {download.started&&<p role="status" className="text-xs text-emerald-700">已开始下载，请查看浏览器下载列表。</p>}
    <p className="text-xs leading-5 text-muted-foreground">自写笔记请放在 BookDistill 目录外。重复解压会覆盖导出文件，不会清理旧产物。</p>
  </section>;
}

export function UploadedImagesView({onConfigureKey}:{onConfigureKey:()=>void}){
  const [listing,setListing]=useState<ImageAssetList>({items:[],total:0,limit:PAGE_SIZE,offset:0});
  const [offset,setOffset]=useState(0);
  const [file,setFile]=useState<File|null>(null);
  const [uploading,setUploading]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<string|null>(null);
  const [uploadError,setUploadError]=useState<string|null>(null);
  const [uploadedId,setUploadedId]=useState<string|null>(null);
  const [revision,refresh]=usePrivateRevision();
  const uploadRequest=useRef<AbortController|null>(null);
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    const changed=()=>{uploadRequest.current?.abort();uploadRequest.current=null;setUploading(false);setUploadError(null);setUploadedId(null);setOffset(0)};
    window.addEventListener(CONNECTION_CHANGE_EVENT,changed);
    return()=>{uploadRequest.current?.abort();uploadRequest.current=null;window.removeEventListener(CONNECTION_CHANGE_EVENT,changed)};
  },[]);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError(null);
    bookDistillApi.listUploadedImages(PAGE_SIZE,offset,controller.signal).then(values=>{
      if(controller.signal.aborted)return;
      if(offset>0&&offset>=values.total){setOffset(Math.max(0,Math.floor((values.total-1)/PAGE_SIZE)*PAGE_SIZE));return}
      setListing(values);
    }).catch(cause=>{if(!controller.signal.aborted){setListing({items:[],total:0,limit:PAGE_SIZE,offset});setError(privateContentError(cause))}})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[offset,revision]);
  const upload=async()=>{
    if(!file||uploadRequest.current)return;
    const controller=new AbortController();uploadRequest.current=controller;setUploading(true);setUploadError(null);setUploadedId(null);
    try{
      const image=await bookDistillApi.uploadImage(file,controller.signal);
      if(controller.signal.aborted)return;
      setUploadedId(image.asset_id);setFile(null);if(input.current)input.current.value="";
      setOffset(Math.floor(listing.total/PAGE_SIZE)*PAGE_SIZE);refresh();
    }catch(cause){if(!controller.signal.aborted)setUploadError(privateContentError(cause))}
    finally{if(uploadRequest.current===controller){uploadRequest.current=null;setUploading(false)}}
  };
  return <div className="mx-auto max-w-[1320px] space-y-6 px-5 py-8 sm:px-8 lg:px-10">
    <section className="rounded-2xl bg-[#182238] p-6 text-white"><h2 className="text-xl font-semibold">图片资产</h2><p className="mt-2 text-sm leading-6 text-slate-300">保存和查看独立上传的图片。书中图片请在书籍详情中查看。</p></section>
    <section className="space-y-4 rounded-2xl border bg-white p-5"><h3 className="font-semibold">上传图片</h3><p className="text-sm text-muted-foreground">支持静态 PNG、JPEG 和 WebP。图片独立保存，无需创建书籍。</p>
      <div className="flex flex-wrap items-center gap-3"><input ref={input} type="file" accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" disabled={uploading} aria-label="选择要上传的图片" onChange={event=>{setFile(event.target.files?.[0]??null);setUploadError(null);setUploadedId(null)}} className="min-w-0 max-w-full text-sm"/><Button disabled={!file||uploading} onClick={()=>void upload()}><ImagePlus className="size-4"/>{uploading?"正在上传…":"上传并保存"}</Button></div>
      {uploadError&&<div className="space-y-2"><ErrorPanel message={uploadError}/><AccessHelp onConfigureKey={onConfigureKey}/></div>}
      {uploadedId&&<p role="status" className="break-all text-sm text-emerald-700">已保存图片：{uploadedId}</p>}
    </section>
    <section><div className="mb-4 flex items-center justify-between"><h3 className="text-lg font-semibold">已上传图片</h3><Button size="sm" variant="outline" onClick={refresh} disabled={loading}><RefreshCw className="size-4"/>刷新</Button></div>
      {loading?<LoadingState label="正在读取上传资产"/>:error?<div className="space-y-3"><ErrorPanel message={error} onRetry={refresh}/><AccessHelp onConfigureKey={onConfigureKey}/></div>:listing.items.length?<><ImageGallery images={listing.items} revision={revision} onConfigureKey={onConfigureKey}/><Pagination offset={offset} total={listing.total} onChange={setOffset}/></>:<EmptyState title="还没有上传图片" description="选择一张图片上传后，即可在这里预览和下载。"/>}
    </section>
  </div>;
}
