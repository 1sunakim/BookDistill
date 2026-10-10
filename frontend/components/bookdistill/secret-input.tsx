"use client";

import { useState, type ComponentProps } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";

type SecretInputProps = Omit<ComponentProps<"input">, "type"> & { label:string };

export function SecretInput({label,className="",...props}:SecretInputProps) {
  const [visible,setVisible]=useState(false);
  return <div className="flex gap-2">
    <input {...props} type={visible?"text":"password"} className={`field min-w-0 flex-1 ${className}`}/>
    <Button type="button" variant="outline" size="icon" aria-label={`${visible?"隐藏":"显示"}${label}`} aria-pressed={visible} title={`${visible?"隐藏":"显示"}${label}`} onClick={()=>setVisible(value=>!value)}>
      {visible?<EyeOff className="size-4"/>:<Eye className="size-4"/>}
    </Button>
  </div>;
}
