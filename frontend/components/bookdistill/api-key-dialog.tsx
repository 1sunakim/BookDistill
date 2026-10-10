"use client";

import { useEffect, useState, type FormEvent } from "react";
import { KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { bookDistillApi, getApiBase, getApiKey, hasApiKey, setApiKey, verifyApiKey, type ModelProfile, type ModelProfileInput } from "@/lib/bookdistill-api";
import { initialModelProfile, modelPresets } from "@/lib/model-presets";
import { SecretInput } from "./secret-input";

const defaultAccessKey=process.env.NEXT_PUBLIC_BOOKDISTILL_DEFAULT_API_KEY ?? "";

function profileForEditing(profile:ModelProfile):ModelProfileInput {
  const preset=modelPresets.find(item=>item.baseUrl===profile.base_url);
  return {
    profile_id:profile.profile_id==="environment"?undefined:profile.profile_id,
    name:profile.name,
    provider:profile.profile_id==="environment"?(preset?.id??"custom"):profile.provider,
    model:profile.model,
    base_url:profile.base_url,
    response_format:profile.response_format,
    api_key:"",
  };
}

export function ApiKeyDialog({open,onOpenChange,onConfigured}:{open:boolean;onOpenChange:(open:boolean)=>void;onConfigured:()=>void}) {
  const [accessKey,setAccessKey]=useState<string|null>(null);
  const [modelForm,setModelForm]=useState<ModelProfileInput>(()=>initialModelProfile());
  const [error,setError]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const selectedPreset=modelPresets.find(item=>item.id===modelForm.provider);
  const displayedAccessKey=accessKey??(getApiKey()||defaultAccessKey);

  useEffect(()=>{
    if(!open||!hasApiKey())return;
    let active=true;
    bookDistillApi.getModelConfiguration().then(configuration=>{
      if(!active)return;
      const selected=configuration.profiles.find(item=>item.profile_id===configuration.active_profile_id);
      if(selected)setModelForm(profileForEditing(selected));
    }).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:"无法读取当前模型配置。")});
    return()=>{active=false};
  },[open]);

  const close=()=>{
    setAccessKey(null);setModelForm(initialModelProfile());setError(null);onOpenChange(false);
  };
  const authenticate=async()=>{
    if(displayedAccessKey.trim()){
      await verifyApiKey(displayedAccessKey);
      setApiKey(displayedAccessKey);
      setAccessKey(null);
    }
    if(!hasApiKey())throw new Error("请填写后端访问 Key（BOOKDISTILL_API_KEY）。");
  };
  const connect=async()=>{
    setBusy(true);setError(null);
    try{
      await authenticate();
      const configuration=await bookDistillApi.getModelConfiguration();
      const selected=configuration.profiles.find(item=>item.profile_id===configuration.active_profile_id);
      if(selected)setModelForm(profileForEditing(selected));
      onConfigured();
    }catch(cause){setError(cause instanceof Error?cause.message:"后端访问 Key 验证失败。")}
    finally{setBusy(false)}
  };
  const submit=async(event:FormEvent<HTMLFormElement>)=>{
    event.preventDefault();setBusy(true);setError(null);
    try{
      await authenticate();
      if(!modelForm.api_key.trim()&&!modelForm.profile_id){
        throw new Error("新模型需要填写模型 API Key；本地模型可使用占位值。若只需设置后端访问 Key，请点击“仅连接后端”。");
      }
      await bookDistillApi.saveModelProfile({
        ...modelForm,
        name:modelForm.name.trim(),model:modelForm.model.trim(),base_url:modelForm.base_url.trim(),
      });
      onConfigured();
      close();
    }catch(cause){setError(cause instanceof Error?cause.message:"无法保存模型配置。")}
    finally{setBusy(false)}
  };
  const clear=()=>{
    setApiKey("");setAccessKey("");setError("已清除当前会话的后端访问 Key。");onConfigured();
  };
  const setField=<K extends keyof ModelProfileInput>(key:K,value:ModelProfileInput[K])=>{
    setModelForm(current=>({...current,[key]:value}));
  };
  const choosePreset=(id:string)=>{
    const preset=modelPresets.find(item=>item.id===id)??modelPresets[modelPresets.length-1];
    setModelForm(initialModelProfile(preset));
  };

  return <Dialog open={open} onOpenChange={next=>{if(!next&&!busy)close()}}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
    <DialogHeader><DialogTitle className="flex items-center gap-2"><KeyRound className="size-5"/>配置访问 Key 与模型</DialogTitle><DialogDescription>在这里同时填写后端访问 Key、模型密钥和调用信息。保存模型后，新提问立即使用该配置。</DialogDescription></DialogHeader>
    <form onSubmit={event=>void submit(event)} className="space-y-4">
      <div><label htmlFor="manual-api-key" className="text-sm font-medium">后端访问 Key（BOOKDISTILL_API_KEY）</label><div className="mt-2"><SecretInput id="manual-api-key" label="后端访问 Key" autoComplete="off" value={displayedAccessKey} onChange={event=>setAccessKey(event.target.value)} placeholder={hasApiKey()?"本次会话已配置；留空继续使用":"输入服务器 .env 中的后端访问 Key"}/></div><p className="mt-1 text-xs text-muted-foreground">用于访问 BookDistill；本机验收环境预填测试 Key，连接后仅保存在当前浏览器会话。当前 API：{getApiBase()}</p></div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div><label htmlFor="dialog-model-provider" className="text-sm font-medium">服务商预设</label><select id="dialog-model-provider" className="field mt-2 w-full" value={modelForm.provider} onChange={event=>choosePreset(event.target.value)}>{modelPresets.map(preset=><option key={preset.id} value={preset.id}>{preset.label}</option>)}{!selectedPreset&&<option value={modelForm.provider}>{modelForm.provider}</option>}</select></div>
        <div><label htmlFor="dialog-model-key" className="text-sm font-medium">模型 API Key</label><div className="mt-2"><SecretInput id="dialog-model-key" label="模型 API Key" autoComplete="off" value={modelForm.api_key} onChange={event=>setField("api_key",event.target.value)} placeholder={modelForm.profile_id?"留空保留原密钥":"输入服务商密钥"}/></div></div>
        <div><label htmlFor="dialog-model-name" className="text-sm font-medium">模型名称</label><input id="dialog-model-name" required list="dialog-model-suggestions" value={modelForm.model} onChange={event=>setField("model",event.target.value)} className="field mt-2 w-full"/><datalist id="dialog-model-suggestions">{selectedPreset?.models.map(model=><option key={model} value={model}/>)}</datalist></div>
        <div><label htmlFor="dialog-profile-name" className="text-sm font-medium">配置名称</label><input id="dialog-profile-name" required value={modelForm.name} onChange={event=>setField("name",event.target.value)} className="field mt-2 w-full"/></div>
      </div>
      <div><label htmlFor="dialog-model-url" className="text-sm font-medium">模型调用地址（Base URL）</label><input id="dialog-model-url" type="url" required value={modelForm.base_url} onChange={event=>setField("base_url",event.target.value)} placeholder="https://example.com/v1" className="field mt-2 w-full"/></div>
      <div><label htmlFor="dialog-model-format" className="text-sm font-medium">结构化输出格式</label><select id="dialog-model-format" value={modelForm.response_format} onChange={event=>setField("response_format",event.target.value as "json_object"|"json_schema")} className="field mt-2 w-full"><option value="json_object">JSON Object</option><option value="json_schema">JSON Schema（需服务商支持）</option></select></div>
      <p className="text-xs leading-5 text-muted-foreground">模型密钥保存在后端数据卷，网页不会回显。更换服务商或调用地址时需重新填写模型密钥。自定义模型和多个档案可在左侧“模型配置”页管理。</p>
      {error&&<p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
      <DialogFooter className="flex-wrap border-t pt-4"><Button type="button" variant="outline" onClick={close} disabled={busy}>取消</Button>{hasApiKey()&&<Button type="button" variant="outline" onClick={clear} disabled={busy}>清除后端 Key</Button>}<Button type="button" variant="outline" onClick={()=>void connect()} disabled={busy||(!displayedAccessKey.trim()&&!hasApiKey())}>仅连接后端</Button><Button type="submit" disabled={busy||!modelForm.model.trim()||!modelForm.base_url.trim()||(!displayedAccessKey.trim()&&!hasApiKey())}>{busy?"保存中…":"保存并启用模型"}</Button></DialogFooter>
    </form>
  </DialogContent></Dialog>;
}
