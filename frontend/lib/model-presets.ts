import type { ModelProfileInput } from "./bookdistill-api";

export type ModelPreset = {id:string;label:string;baseUrl:string;models:string[];format:"json_object"|"json_schema"};

export const modelPresets:ModelPreset[]=[
  {id:"deepseek",label:"DeepSeek",baseUrl:"https://api.deepseek.com",models:["deepseek-flash","deepseek-v4-pro"],format:"json_object"},
  {id:"openai",label:"OpenAI",baseUrl:"https://api.openai.com/v1",models:["gpt-4.1-mini","gpt-4.1"],format:"json_schema"},
  {id:"gemini",label:"Google Gemini",baseUrl:"https://generativelanguage.googleapis.com/v1beta/openai",models:["gemini-3.8-flash"],format:"json_object"},
  {id:"qwen",label:"阿里云百炼 / Qwen",baseUrl:"https://dashscope.aliyuncs.com/compatible-mode/v1",models:["qwen-plus"],format:"json_object"},
  {id:"openrouter",label:"OpenRouter",baseUrl:"https://openrouter.ai/api/v1",models:["~openai/gpt-latest"],format:"json_object"},
  {id:"ollama",label:"Ollama 本地",baseUrl:"http://host.docker.internal:11434/v1",models:["qwen2.5:7b"],format:"json_object"},
  {id:"custom",label:"自定义 OpenAI 兼容接口",baseUrl:"",models:[],format:"json_object"},
];

export function initialModelProfile(preset=modelPresets[0]):ModelProfileInput {
  return {name:preset.label,provider:preset.id,model:preset.models[0]??"",base_url:preset.baseUrl,response_format:preset.format,api_key:preset.id==="ollama"?"ollama":""};
}
