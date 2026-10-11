import {useEffect,useRef,useState,useId,ReactNode} from 'react';
import {X,LoaderCircle,AlertCircle,ShieldCheck,ArrowUpRight,Check,Search} from 'lucide-react';
import {api,RecordData,modes,money} from './api';
export function useLoad(path:string,revision=0){
 const [data,setData]=useState<RecordData|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 useEffect(()=>{let alive=true;setLoading(true);setError('');api(path).then(v=>{if(alive)setData(v);}).catch(e=>{if(alive)setError(e.message);}).finally(()=>{if(alive)setLoading(false);});return()=>{alive=false;};},[path,revision]);
 return {data,error,loading};
}
export function Empty({title='Пока ничего нет',text='Попробуйте изменить фильтры.',children}:{title?:string;text?:string;children?:ReactNode}){
 return <div className="empty"><Search size={30}/><h3>{title}</h3><p>{text}</p>{children}</div>;
}
export function Loading(){return <div className="loading" role="status"><LoaderCircle className="spin" size={23}/> Загружаем данные…</div>;}
export function ErrorBox({error}:{error:string}){return error?<div className="error" role="alert"><AlertCircle size={18}/>{error}</div>:null;}
export function Modal({title,children,onClose,wide=false}:{title:string;children:ReactNode;onClose:()=>void;wide?:boolean}){
 const ref=useRef<HTMLDivElement>(null),close=useRef(onClose),titleId=useId();close.current=onClose;
 useEffect(()=>{const old=document.activeElement as HTMLElement;const bodyOverflow=document.body.style.overflow;document.body.style.overflow='hidden';ref.current?.focus();
 const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')close.current();if(e.key==='Tab'){
 const nodes=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')||[]).filter(node=>node.getClientRects().length>0);
 if(!nodes?.length)return;const first=nodes[0],last=nodes[nodes.length-1];
 if(e.shiftKey&&(document.activeElement===first||document.activeElement===ref.current)){e.preventDefault();last.focus();}
 else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}};
 document.addEventListener('keydown',handler);return()=>{document.removeEventListener('keydown',handler);document.body.style.overflow=bodyOverflow;old?.focus();};},[]);
 return <div className="overlay" onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}><div ref={ref} tabIndex={-1} className={'modal '+(wide?'wide':'')} role="dialog" aria-modal="true" aria-labelledby={titleId}><header><h2 id={titleId}>{title}</h2><button className="icon-btn" aria-label="Закрыть" onClick={onClose}><X/></button></header><div className="modal-body">{children}</div></div></div>;
}
export function Badge({children,type='neutral'}:{children:ReactNode;type?:string}){return <span className={'badge '+type}>{children}</span>;}
export function Tag({children}:{children:ReactNode}){return <span className="tag">{children}</span>;}
export function Avatar({item,size=''}:{item:RecordData;size?:string}){return <span className={'avatar '+size} style={{background:item.color||'#e4d9f7'}}>{item.initials||item.name?.split(' ').map((v:string)=>v[0]).slice(0,2).join('')}</span>;}
export function Score({match}:{match:RecordData}){return <div className="scores"><div><span>Покрытие требований</span><strong>{match.coverage}%</strong><div className="bar"><i style={{width:match.coverage+'%'}}/></div></div><div><span>Из источников {match.matrix?.some((r:RecordData)=>r.status==='demo_source')&&<small>· демо</small>}</span><strong>{match.evidence_coverage}%</strong><div className="bar evidence"><i style={{width:match.evidence_coverage+'%'}}/></div></div></div>;}
export function SourceDialog({id,onClose}:{id:string;onClose:()=>void}){
 const {data,error,loading}=useLoad('/achievements/'+encodeURIComponent(id));
 return <Modal title="Источник достижения" onClose={onClose}><ErrorBox error={error}/>{loading?<Loading/>:data&&<>
 <Badge type={data.active?'purple':'neutral'}>{data.demo?'Тестовая запись ФСП':'Запись источника'} · {data.active?'Активна':'Отозвана'}</Badge>
 <h3>{data.title}</h3><p>{data.contest}</p><dl className="facts"><div><dt>Результат</dt><dd>{data.result}</dd></div><div><dt>Дата</dt><dd>{data.date}</dd></div><div><dt>Роль в команде</dt><dd>{data.role_label||'Не установлена'}</dd></div><div><dt>Спортивный разряд</dt><dd>{data.sports_rank||'Не указан'}</dd></div><div><dt>Идентификатор</dt><dd>{data.id} · версия {data.version}</dd></div></dl>
 <div className="note"><ShieldCheck size={20}/><div>{data.note||'Навыки связываются только с явно указанным индивидуальным вкладом.'}<p>Спортивный результат сам по себе не подтверждает профессиональный грейд.</p></div></div>
 <details><summary>Посмотреть исходную запись</summary><pre>{JSON.stringify(data,null,2)}</pre></details></>}</Modal>;
}
export function Matrix({match,onSource}:{match:RecordData;onSource:(id:string)=>void}){
 return <><Score match={match}/><p className="muted small">Покрытие учитывает заявленные навыки и источники. Это не вероятность найма. Обязательный навык имеет вес 2, желательный — 1.</p>
 <div className="matrix">{match.matrix.map((row:RecordData)=><div className="matrix-row" key={row.skill}><div><strong>{row.label}</strong><small>{row.required?'Обязательно':'Желательно'}</small></div><div>{row.status==='unknown'?<Badge>Нет данных</Badge>:row.status==='claimed'?<Badge type="amber">Заявлено</Badge>:<Badge type="green"><ShieldCheck size={13}/>{row.status==='demo_source'?'Источник · демо':'Источник'}</Badge>}
 {row.evidence.map((e:RecordData)=><button key={e.id} className="source-link" onClick={()=>onSource(e.id)}>{e.contest}<ArrowUpRight size={13}/></button>)}</div></div>)}</div>
 {!!match.missing.length&&<div className="note amber-note">Нужно уточнить: {match.missing.join(', ')}.</div>}
 {match.constraints.blocked.map((s:string)=><div className="error" key={s}>{s}</div>)}
 {match.constraints.unknown.map((s:string)=><p className="muted small" key={s}>{s}</p>)}
 <p className="muted small">Версия профиля {match.profile_version} · версия вакансии {match.vacancy_version}</p></>;
}
export function MatchDialog({candidateId,vacancyId,onClose,onSource}:{candidateId:string;vacancyId:string;onClose:()=>void;onSource:(id:string)=>void}){
 const {data,loading,error}=useLoad('/match?candidate_id='+candidateId+'&vacancy_id='+vacancyId);
 return <Modal title="Почему подходит" onClose={onClose} wide><ErrorBox error={error}/>{loading?<Loading/>:data&&<><p className="eyebrow">{data.candidate.name} → {data.vacancy.title}</p><Matrix match={data.match} onSource={onSource}/></>}</Modal>;
}
export function SkillsSelect({skills,selected,onChange}:{skills:RecordData[];selected:string[];onChange:(ids:string[])=>void}){
 return <div className="skill-select">{selected.map(id=><button key={id} type="button" className="chosen" aria-label={'Удалить '+(skills.find(s=>s.id===id)?.label||id)} onClick={()=>onChange(selected.filter(x=>x!==id))}>{skills.find(s=>s.id===id)?.label||id}<X size={12}/></button>)}<select aria-label="Добавить навык" value="" onChange={e=>{if(e.target.value)onChange([...selected,e.target.value]);}}><option value="">+ Добавить навык</option>{skills.filter(s=>!selected.includes(s.id)).map(s=><option key={s.id} value={s.id}>{s.label}</option>)}</select></div>;
}
export function Salary({item}:{item:RecordData}){return <div className="salary">{money(item)}<small>{item.salary_type==='gross'?'до вычета налогов':'на руки'} / месяц</small></div>;}
export function WorkMode({item}:{item:RecordData}){return <span>{modes[item.work_mode]||'Уточняется'}{item.city?' · '+item.city:''}</span>;}
