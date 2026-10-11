import {useEffect,useState} from 'react';
// A deterministic release marker also refreshes clients when backend policy defaults change.
document.documentElement.dataset.fspRelease='0.13.0';

// Check the deployed bundle, including frontend-only releases. Never reload a draft automatically.
export function useInterfaceUpdate(){
 const [available,setAvailable]=useState(false);
 useEffect(()=>{
  const selector='script[src^="/assets/index-"][src$=".js"]';
  const current=document.querySelector<HTMLScriptElement>(selector)?.getAttribute('src');
  if(!current)return;
  let stopped=false,busy=false,controller:AbortController|null=null;
  const check=async()=>{
   if(stopped||busy||document.visibilityState==='hidden'||!navigator.onLine)return;
   busy=true;controller=new AbortController();
   const timeout=window.setTimeout(()=>controller?.abort(),10000);
   try{
    const response=await fetch(location.pathname,{cache:'no-store',credentials:'same-origin',redirect:'error',headers:{Accept:'text/html'},signal:controller.signal});
    if(!response.ok||!response.headers.get('content-type')?.includes('text/html'))return;
    const next=new DOMParser().parseFromString(await response.text(),'text/html').querySelector<HTMLScriptElement>(selector)?.getAttribute('src');
    if(!stopped&&next&&next!==current)setAvailable(true);
   }catch{/* A connection failure must not interrupt the current task. */}
   finally{window.clearTimeout(timeout);busy=false;}
  };
  void check();
  const wake=()=>{void check();};
  const timer=window.setInterval(wake,60000);
  window.addEventListener('focus',wake);window.addEventListener('online',wake);document.addEventListener('visibilitychange',wake);
  return()=>{stopped=true;controller?.abort();window.clearInterval(timer);window.removeEventListener('focus',wake);window.removeEventListener('online',wake);document.removeEventListener('visibilitychange',wake);};
 },[]);
 return available;
}

export function InterfaceUpdateNotice({available}:{available:boolean}){
 if(!available)return null;
 return <div className="interface-update" role="status"><span><strong>Доступна новая версия сервиса.</strong> Сохраните текущие изменения, затем обновите страницу.</span><button className="btn secondary" onClick={()=>location.reload()}>Обновить страницу</button></div>;
}
