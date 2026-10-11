import {useEffect} from 'react';
import {Code2,ShieldCheck,Send,Check} from 'lucide-react';

export function useDesignMotion(){
 useEffect(()=>{
  const preference=window.matchMedia('(prefers-reduced-motion: reduce)');
  const update=()=>{document.documentElement.dataset.designMotion=document.hidden||preference.matches?'paused':'running';};
  update();document.addEventListener('visibilitychange',update);preference.addEventListener('change',update);
  return()=>{document.removeEventListener('visibilitychange',update);preference.removeEventListener('change',update);delete document.documentElement.dataset.designMotion;};
 },[]);
}

export function CareerScene(){
 return <div className="career-scene">
  <div className="scene-art" aria-hidden="true">
   <div className="scene-halo"/><div className="scene-grid"/>
   <svg className="scene-network" viewBox="0 0 540 400" fill="none">
    <defs><linearGradient id="career-network-color" x1="90" y1="70" x2="450" y2="330"><stop stopColor="#9b91ff"/><stop offset=".55" stopColor="#402fff"/><stop offset="1" stopColor="#ec1d35"/></linearGradient></defs>
    <g className="scene-orbits" stroke="url(#career-network-color)">
     <ellipse cx="270" cy="196" rx="151" ry="119" opacity=".6"/>
     <ellipse cx="270" cy="196" rx="151" ry="44" opacity=".4"/>
     <ellipse cx="270" cy="196" rx="62" ry="119" opacity=".45"/>
     <ellipse cx="270" cy="196" rx="112" ry="119" opacity=".25"/>
     <path d="M121 183 232 83 355 120 409 246 297 311 172 274 121 183ZM232 83 172 274M355 120 297 311M121 183 409 246M232 83 409 246M172 274 355 120" opacity=".25"/>
    </g>
    <path className="scene-signal" d="M77 130 168 149 270 196 398 216 468 137M125 328 226 273 270 196 319 65" stroke="url(#career-network-color)" strokeWidth="2" strokeDasharray="8 14"/>
    {[[121,183],[232,83],[355,120],[409,246],[297,311],[172,274],[319,65],[77,130],[468,137],[125,328]].map(([x,y],index)=><g key={index}><circle cx={x} cy={y} r="4" fill={index%3===0?'#ec1d35':'#a49aff'}/><circle className="scene-ping" cx={x} cy={y} r="9" stroke={index%3===0?'#ec1d35':'#a49aff'} style={{animationDelay:index*.35+'s'}}/></g>)}
    <path d="M90 75h-18v18M450 75h18v18M90 327H72v-18M450 327h18v-18" stroke="#a8aabd" opacity=".4"/>
   </svg>
   <div className="scene-core"><span className="scene-core-icon"><ShieldCheck size={27}/></span><div><small>ПРОВЕРЯЕМЫЙ ОПЫТ</small><strong>Паспорт результата</strong><span>Навык · кейс · источник</span></div><Check size={17}/></div>
   <div className="scene-label scene-label-skill"><Code2 size={18}/><div><small>01 / НАВЫК</small><strong>Покажите, что умеете</strong></div></div>
   <div className="scene-label scene-label-proof"><ShieldCheck size={18}/><div><small>02 / ПРОВЕРКА</small><strong>Решите рабочий кейс</strong></div></div>
   <div className="scene-label scene-label-contact"><Send size={18}/><div><small>03 / КОНТАКТ</small><strong>Получите приглашение</strong></div></div>
  </div>
  <div className="scene-caption"><span>От навыка — к следующему шагу</span></div>
 </div>;
}
