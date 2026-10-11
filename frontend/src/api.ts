export type RecordData = Record<string, any>;
let csrf = '';
export const setCsrf = (value:string) => {csrf=value;};
export async function api(path:string,body?:unknown,method?:string):Promise<any> {
  const options:RequestInit={method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',headers:{'X-CSRF-Token':csrf}};
  if(body instanceof FormData) options.body=body;
  else if(body!==undefined){(options.headers as Record<string,string>)['Content-Type']='application/json';options.body=JSON.stringify(body);}
  const controller=new AbortController();options.signal=controller.signal;
  const timer=setTimeout(()=>controller.abort(),30000);
  let r:Response;
  try{r=await fetch('/api'+path,options);}catch(e:any){throw new Error(e.name==='AbortError'?'Сервер не ответил вовремя. Проверьте состояние и повторите действие.':'Нет соединения. Проверьте сеть и повторите действие.');}finally{clearTimeout(timer);}
  if(!r.ok){if(r.status===401&&!['/auth/login','/auth/register','/auth/me'].includes(path))window.dispatchEvent(new Event('fsp-session-invalid'));const data=await r.json().catch(()=>({detail:'Не удалось выполнить действие. Повторите попытку.'}));const error=Object.assign(new Error(errorMessage(data.detail)),{status:r.status});throw error;}
  return r.status===204?null:r.json();
}
const fieldLabels:Record<string,string>={username:'Логин',email:'Email',password:'Пароль',name:'Имя',title:'Название',description:'Описание',team:'Команда',skills:'Навыки',salary_min:'Зарплата от',salary_max:'Зарплата до',employer_contact:'Способ связи',next_step:'Следующий шаг',response_days:'Срок контакта',message:'Сообщение',city:'Город',role:'Направление',specialization:'Направление',grade:'Уровень',answer:'Ответ',version:'Данные'};
function errorMessage(detail:unknown):string{
 if(typeof detail==='string')return detail;
 if(!Array.isArray(detail))return 'Не удалось выполнить действие. Проверьте данные и повторите.';
 return detail.slice(0,3).map(entry=>{const item=entry&&typeof entry==='object'?entry:{};const field=item.loc?.[item.loc.length-1];const label=fieldLabels[field]||'Поле формы';const ctx=item.ctx||{};let text='проверьте введённое значение';
 if(item.type==='missing')text='заполните поле';
 else if(item.type==='string_too_short')text=`нужно не менее ${ctx.min_length} символов`;
 else if(item.type==='string_too_long')text=`допустимо не более ${ctx.max_length} символов`;
 else if(item.type==='too_short')text=`выберите не менее ${ctx.min_length} значения`;
 else if(item.type==='greater_than_equal')text=`значение должно быть не меньше ${ctx.ge}`;
 else if(item.type==='less_than_equal')text=`значение должно быть не больше ${ctx.le}`;
 else if(typeof item.msg==='string'&&/[А-Яа-яЁё]/.test(item.msg))text=item.msg.replace(/^Value error,\s*/, '');
 return label+': '+text+'.';}).join(' ');
}
export const modes:Record<string,string>={remote:'Удалённо',hybrid:'Гибрид',office:'Офис'};
export function money(item:RecordData){return [item.salary_min,item.salary_max].filter(v=>v!=null).map(v=>Number(v).toLocaleString('ru-RU')).join(' – ')+' ₽';}
