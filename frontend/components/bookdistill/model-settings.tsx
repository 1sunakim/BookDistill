"use client";

import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { Check, KeyRound, Plus, RefreshCw, Save, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { bookDistillApi, getApiKey, hasApiKey, setApiKey, verifyApiKey, type ModelConfiguration, type ModelProfile, type ModelProfileInput } from "@/lib/bookdistill-api";
import { initialModelProfile as initial, modelPresets as presets } from "@/lib/model-presets";
import { SecretInput } from "./secret-input";

const defaultAccessKey=process.env.NEXT_PUBLIC_BOOKDISTILL_DEFAULT_API_KEY ?? "";

export function ModelSettings(){
  const [configuration,setConfiguration]=useState<ModelConfiguration|null>(null);
  const [form,setForm]=useState<ModelProfileInput>(()=>initial());
  const [accessKey,setAccessKey]=useState<string|null>(null);
  const [accessKeyConfigured,setAccessKeyConfigured]=useState(()=>hasApiKey());
  const displayedAccessKey=accessKey??(getApiKey()||defaultAccessKey);
  const [importText,setImportText]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [notice,setNotice]=useState<string|null>(null);
  const load=useCallback(async()=>{
    try{setConfiguration(await bookDistillApi.getModelConfiguration());setError(null)}
    catch(cause){setError(cause instanceof Error?cause.message:"无法读取模型配置。")}
  },[]);
  useEffect(()=>{
    let active=true;
    bookDistillApi.getModelConfiguration()
      .then(value=>{if(active)setConfiguration(value)})
      .catch(cause=>{if(active)setError(cause instanceof Error?cause.message:"无法读取模型配置。")});
    return()=>{active=false};
  },[]);
  const setField=<K extends keyof ModelProfileInput>(key:K,value:ModelProfileInput[K])=>setForm(current=>({...current,[key]:value}));
  const choosePreset=(id:string)=>{const preset=presets.find(item=>item.id===id)??presets[6];setForm(initial(preset));setNotice(null)};
  const edit=(profile:ModelProfile)=>{
    setForm({profile_id:profile.profile_id,name:profile.name,provider:profile.provider,model:profile.model,base_url:profile.base_url,response_format:profile.response_format,api_key:""});
    setError(null);setNotice(null);
    document.getElementById("model-profile-form")?.scrollIntoView({behavior:"smooth",block:"start"});
  };
  const connect=async()=>{
    if(!displayedAccessKey.trim())return;
    setBusy(true);setError(null);setNotice(null);
    try{
      await verifyApiKey(displayedAccessKey);
      setApiKey(displayedAccessKey);
      setAccessKeyConfigured(true);
      setAccessKey(null);
      await load();
      setNotice("后端访问 Key 已验证，可以查看和编辑已有模型。密钥只保存在当前浏览器会话。")
    }catch(cause){setError(cause instanceof Error?cause.message:"后端访问 Key 验证失败。")}
    finally{setBusy(false)}
  };
  const save=async(event:FormEvent)=>{
    event.preventDefault();setBusy(true);setError(null);setNotice(null);
    try{
      if(displayedAccessKey.trim()){
        await verifyApiKey(displayedAccessKey);
        setApiKey(displayedAccessKey);
        setAccessKeyConfigured(true);
        setAccessKey(null);
      }
      if(!hasApiKey())throw new Error("请先填写后端访问 Key（BOOKDISTILL_API_KEY）。");
      const profile=await bookDistillApi.saveModelProfile({...form,name:form.name.trim(),model:form.model.trim(),base_url:form.base_url.trim()});
      await load();setForm(initial(presets.find(item=>item.id===form.provider)??presets[6]));
      setNotice(`已保存并启用 ${profile.name}；新请求会立即使用此模型。`);
    }catch(cause){setError(cause instanceof Error?cause.message:"保存失败。")}
    finally{setBusy(false)}
  };
  const activate=async(profile:ModelProfile)=>{
    setBusy(true);setError(null);setNotice(null);
    try{await bookDistillApi.activateModelProfile(profile.profile_id);await load();setNotice(`已启用 ${profile.name}。`)}
    catch(cause){setError(cause instanceof Error?cause.message:"切换失败。")}
    finally{setBusy(false)}
  };
  const remove=async(profile:ModelProfile)=>{
    if(!window.confirm(`删除模型配置“${profile.name}”？`))return;
    setBusy(true);setError(null);setNotice(null);
    try{await bookDistillApi.deleteModelProfile(profile.profile_id);await load();if(form.profile_id===profile.profile_id)setForm(initial());setNotice("模型配置已删除。")}
    catch(cause){setError(cause instanceof Error?cause.message:"删除失败。")}
    finally{setBusy(false)}
  };
  const importJson=()=>{
    try{
      const data=JSON.parse(importText) as Record<string,unknown>;
      if(!data||typeof data!=="object"||typeof data.model!=="string"||typeof data.base_url!=="string")throw new Error("JSON 至少需要 model 和 base_url 字段。");
      const capability=["json_schema","json_object","tool_calling","text_parser"].includes(String(data.response_format))?String(data.response_format) as ModelProfileInput["response_format"]:"json_object";
      setForm({name:String(data.name??"自定义模型"),provider:String(data.provider??"custom"),model:data.model,base_url:data.base_url,response_format:capability,api_key:String(data.api_key??"")});
      setError(null);setNotice("已导入到表单；请核对后点击“保存并启用”。");
    }catch(cause){setError(cause instanceof Error?cause.message:"JSON 格式无效。")}
  };
  const importFile=async(event:ChangeEvent<HTMLInputElement>)=>{
    const file=event.target.files?.[0];if(file)setImportText(await file.text());event.target.value="";
  };
  const selectedPreset=presets.find(item=>item.id===form.provider);
  return <div className="mx-auto max-w-[1200px] px-5 py-7 sm:px-8 lg:px-10">
    <div className="rounded-2xl bg-[#182238] p-6 text-white"><div className="flex items-center gap-3"><KeyRound className="size-6 text-indigo-200"/><h2 className="text-xl font-semibold">模型配置</h2></div><p className="mt-2 text-sm leading-6 text-slate-300">在同一表单填写后端访问 Key、模型密钥及模型信息，一次保存并启用。模型密钥保存在后端数据卷中，网页只显示是否已设置。</p></div>
    {error&&<div role="alert" className="mt-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}{!configuration&&<p className="mt-2">可在下方表单直接填写后端访问 Key；若服务器尚未设置 BOOKDISTILL_API_KEY，需先在服务器 .env 设置并重启 API。</p>}</div>}
    {notice&&<div role="status" className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700">{notice}</div>}
    <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <section className="h-fit rounded-2xl border bg-white p-5"><div className="flex items-center justify-between"><h3 className="font-semibold">已保存模型</h3><Button type="button" variant="outline" size="sm" onClick={()=>void load()}><RefreshCw className="size-4"/>刷新</Button></div><p className="mt-2 text-xs text-muted-foreground">服务器 .env 配置始终可切回。新建档案保存后自动启用。</p><div className="mt-4 space-y-3">{configuration?.profiles.map(profile=><div key={profile.profile_id} className={`rounded-xl border p-4 ${configuration.active_profile_id===profile.profile_id?"border-primary bg-primary/5":""}`}><div className="flex items-start justify-between gap-3"><div><p className="font-medium">{profile.name}</p><p className="mt-1 break-all text-xs text-muted-foreground">{profile.model} · {profile.base_url}</p><p className="mt-1 text-xs text-muted-foreground">{profile.has_api_key?"密钥已保存":"无密钥"} · {profile.response_format}</p></div>{configuration.active_profile_id===profile.profile_id&&<span className="shrink-0 rounded-full bg-primary px-2 py-1 text-xs text-white">使用中</span>}</div><div className="mt-3 flex flex-wrap gap-2">{configuration.active_profile_id!==profile.profile_id&&<Button type="button" size="sm" variant="outline" disabled={busy} onClick={()=>void activate(profile)}><Check className="size-3"/>启用</Button>}{profile.profile_id!=="environment"&&<><Button type="button" size="sm" variant="outline" onClick={()=>edit(profile)}>编辑</Button><Button type="button" size="sm" variant="outline" disabled={busy} onClick={()=>void remove(profile)}><Trash2 className="size-3"/>删除</Button></>}</div></div>)}</div></section>
      <section id="model-profile-form" className="rounded-2xl border bg-white p-5"><div className="flex items-center justify-between"><h3 className="font-semibold">{form.profile_id?"编辑模型档案":"导入模型"}</h3><Button type="button" variant="outline" size="sm" onClick={()=>{setForm(initial());setError(null)}}><Plus className="size-4"/>新建</Button></div><p className="mt-2 text-xs leading-5 text-muted-foreground">支持 OpenAI 兼容的 Chat Completions 接口。预设仅填写建议值，模型名、地址和响应格式均可修改。</p>
        <form onSubmit={event=>void save(event)} className="mt-5 space-y-4">
          <div><label htmlFor="model-config-access-key" className="mb-2 block text-sm font-medium">后端访问 Key（BOOKDISTILL_API_KEY）</label><div className="flex gap-2"><div className="min-w-0 flex-1"><SecretInput id="model-config-access-key" label="后端访问 Key" autoComplete="off" value={displayedAccessKey} onChange={e=>setAccessKey(e.target.value)} placeholder={accessKeyConfigured?"本次会话已设置；留空继续使用或输入新值":"输入服务器 .env 中的后端访问 Key"}/></div><Button type="button" variant="outline" disabled={busy||!displayedAccessKey.trim()} onClick={()=>void connect()}>连接并读取</Button></div><p className="mt-1 text-xs text-muted-foreground">可直接随模型一起保存；如需编辑已有模型，先点击“连接并读取”。此 Key 仅保存在当前浏览器会话。</p></div>
          <Field label="服务商预设"><select className="field" value={form.provider} onChange={e=>choosePreset(e.target.value)}>{presets.map(preset=><option key={preset.id} value={preset.id}>{preset.label}</option>)}{!selectedPreset&&<option value={form.provider}>{form.provider}</option>}</select></Field>
          <div><label htmlFor="model-config-model-key" className="mb-2 block text-sm font-medium">模型 API Key</label><SecretInput id="model-config-model-key" label="模型 API Key" autoComplete="off" value={form.api_key} onChange={e=>setField("api_key",e.target.value)} placeholder={form.profile_id?"地址不变时可留空，保留已保存的密钥":"输入服务商密钥；本地服务可使用占位值"}/><span className="mt-1 block text-xs text-muted-foreground">更换服务商或调用地址时必须重新填写密钥。</span></div>
          <Field label="配置名称"><input className="field" required maxLength={100} value={form.name} onChange={e=>setField("name",e.target.value)} placeholder="例如：工作用 DeepSeek"/></Field>
          <Field label="模型名称"><input className="field" required list="model-name-suggestions" value={form.model} onChange={e=>setField("model",e.target.value)} placeholder="输入服务商提供的模型 ID"/><datalist id="model-name-suggestions">{selectedPreset?.models.map(model=><option key={model} value={model}/>)}</datalist></Field>
          <Field label="模型调用地址（Base URL）"><input className="field" required type="url" value={form.base_url} onChange={e=>setField("base_url",e.target.value)} placeholder="https://example.com/v1"/></Field>
          <Field label="模型结构化输出能力"><select className="field" value={form.response_format} onChange={e=>setField("response_format",e.target.value as ModelProfileInput["response_format"])}><option value="json_schema">原生 JSON Schema</option><option value="json_object">JSON Object</option><option value="tool_calling">工具调用 / Function Calling</option><option value="text_parser">纯文本 JSON 解析</option></select><span className="mt-1 block text-xs text-muted-foreground">按模型接口实际能力选择；结构校验与证据引用验证始终由应用执行。</span></Field>
          <p className="text-xs leading-5 text-muted-foreground">请求发送到上方地址的 /chat/completions。配置和密钥写入后端持久化数据卷，读取接口不会回传密钥。选择外部服务商后，提问时的检索片段会发送给该服务商。</p>
          <Button type="submit" disabled={busy||!form.model.trim()||!form.base_url.trim()||(!form.api_key.trim()&&!form.profile_id)} className="w-full"><Save className="size-4"/>{busy?"保存中…":"保存并启用"}</Button>
        </form>
        <details className="mt-6 border-t pt-5"><summary className="cursor-pointer text-sm font-semibold">从 JSON 导入自定义模型</summary><p className="mt-3 text-xs text-muted-foreground">支持字段 name、provider、model、base_url、api_key、response_format。导入后还需核对并保存。</p><pre className="mt-3 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">{`{"name":"我的模型","provider":"custom","model":"my-model","base_url":"https://example.com/v1","api_key":"...","response_format":"json_object"}`}</pre><textarea className="field mt-3 min-h-28 py-3" value={importText} onChange={e=>setImportText(e.target.value)} placeholder="粘贴 JSON 配置"/><div className="mt-3 flex flex-wrap gap-2"><label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm"><Upload className="size-4"/>选择 JSON 文件<input type="file" accept="application/json,.json" className="sr-only" onChange={e=>void importFile(e)}/></label><Button type="button" variant="outline" onClick={importJson} disabled={!importText.trim()}>导入到表单</Button></div></details>
      </section>
    </div>
  </div>;
}

function Field({label,children}:{label:string;children:ReactNode}){return <label className="block"><span className="mb-2 block text-sm font-medium">{label}</span>{children}</label>}
