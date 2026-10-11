import {RecordData} from './api';

export const labels:Record<string,string>={backend:'Backend Python',frontend:'Frontend TypeScript',qa:'QA',devops:'DevOps',ml:'ML и анализ данных',security:'Информационная безопасность'};
export const statusLabels:Record<string,string>={generating:'Готовим задания',active:'Можно отвечать',grading:'Проверяем ответы',completed:'Результат готов',cancelled:'Попытка отменена',sent:'Приглашение отправлено',viewed:'Приглашение просмотрено',accepted:'Приглашение принято',declined:'Приглашение отклонено',clarification:'Уточнение условий',replied:'Ответ работодателя',withdrawn:'Приглашение отозвано',contacted:'Работодатель отметил контакт',contact_confirmed:'Кандидат подтвердил контакт',contact_missing:'Кандидат сообщил, что контакта не было',interview_scheduled:'Встреча назначена',decision_sent:'Решение сообщено'};
export function prettyDate(value:string){return new Date(value).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});}

export function HowItWorks({role,onNavigate}:{role:string;onNavigate:(route:string)=>void}){
 return <><div className="page-title"><h1>Как начать работу</h1></div><div className="career-grid"><section className="panel"><h2>Я ищу работу</h2><ol className="plain-steps"><li><strong>Заполните профиль.</strong> Укажите навыки и ожидания.</li><li><strong>Проверьте уровень.</strong> Тренировка помогает познакомиться с заданиями и не меняет грейд.</li><li><strong>Разрешите подбор.</strong> Управляйте видимостью в профиле.</li><li><strong>Рассмотрите приглашение.</strong> Сначала увидите компанию, зарплату и следующий шаг.</li></ol>{role==='candidate'&&<button className="btn primary" onClick={()=>onNavigate('profile')}>Начать с профиля</button>}</section><section className="panel"><h2>Я нанимаю специалиста</h2><ol className="plain-steps"><li><strong>Проверьте компанию.</strong> Это открывает возможность приглашать.</li><li><strong>Создайте запрос на подбор.</strong> Задачи, уровень, навыки и зарплата.</li><li><strong>Посмотрите подборку.</strong> Основания выбора видны в карточке.</li><li><strong>Пригласите кандидата.</strong> Публиковать вакансию для этого не требуется.</li></ol>{role==='employer'&&<button className="btn primary" onClick={()=>onNavigate('candidates')}>Начать подбор</button>}</section></div><section className="panel"><h2>Контакт — по решению кандидата</h2><p>Контакты открываются выбранной организации после принятия приглашения. Передача в ATS разрешается отдельно. Результаты проверки навыков используют пилотную шкалу. Область и дата проверки указаны в паспорте результата.</p></section></>;
}

export function MatchEvidence({match}:{match:RecordData}){
 const components:Record<string,string>={objective:'Результат заданий',declared_stack:'Совпадение технологий',requested_evidence:'Проверки и источники по навыкам',recency:'Давность проверки',fsp:'Индивидуальные сведения ФСП',adjacent_penalty:'Поправка за соседнюю категорию',unknown_penalty:'Уточнение условий'};
 const states:Record<string,string>={tested_in_session:'Есть проверка в сессии',source:'Есть источник',claimed:'Указано кандидатом',unknown:'Нужно уточнить'};
 return <details className="user-details"><summary>Основания подбора</summary><p className="muted small">Категория основана на заданиях по направлению. Проверка отдельной технологии указывается ниже, если она есть в результате или источнике.</p><div className="evidence-list">{match.matrix?.map((row:RecordData)=><div key={row.skill}><strong>{row.label||row.skill}</strong><span>{states[row.status]||'Нужно уточнить'}</span></div>)}</div>{match.unknown?.length>0&&<p>Нужно уточнить: {match.unknown.join(', ')}.</p>}{match.score_components&&<><h3>Состав балла</h3><div className="evidence-list">{Object.entries(match.score_components).map(([name,value])=><div key={name}><span>{components[name]||name}</span><strong>{Number(value).toLocaleString('ru-RU',{maximumFractionDigits:2})}</strong></div>)}</div><p className="muted small">Это баллы подбора, не вероятность успешного найма. Отсутствие сведений требует уточнения и не доказывает отсутствие навыка.</p></>}<p className="muted">Достижения ФСП дополняют результат проверки. Отсутствие истории ФСП не исключает кандидата.</p></details>;
}

export function AssessmentEvidence({assessment,score}:{assessment:RecordData;score:number}){
 if(!assessment)return <p className="muted">Семантика исторического балла не указана.</p>;
 const stages=assessment.stages,passport=assessment.passport,newPath=assessment.score_semantics==='screening_score';
 return <section className="assessment-evidence" data-session={assessment.session_id||''}>
 {newPath?<><div className="journey-scores"><div><span>Короткий тест</span><strong>{stages?.screening.score??score}%</strong></div><div><span>Рабочий кейс</span><strong>{stages?stages.work_sample.score+'%':'Недоступен'}</strong></div><div><span>Разбор</span><strong>{stages?(stages.review.score==null?(stages.review.method==='not_performed_optional'?'Не проводился':'Ожидается'):stages.review.score+'%'):'Недоступен'}</strong></div></div>
 {!stages&&<p className="muted">Подробные результаты этой аттестации доступны через опубликованный паспорт.</p>}
 {passport&&<p className="muted">Паспорт v{passport.revision} · {new Date(passport.issued_at).toLocaleString('ru-RU')}. {!passport.category_confirmed_by_this_revision&&'Эта ревизия не подтверждает действующую категорию; категория сохраняется по прежнему решению.'}</p>}
 {stages?.transfer&&<p className="muted small">Перенос знаний: {stages.transfer.score}% · {stages.transfer.all_checks_passed?'Проверки пройдены':'Нужна доработка'}</p>}<p className="muted">Отдельные основания по заданиям попытки. Общий балл компетентности не вычисляется; шкала пилотная.</p></>:<p>{assessment.score_label}: {score}% · {assessment.score_semantics==='synthetic_example'?'Тест человеком не подтверждён.':assessment.score_semantics==='legacy_objective_score'?'Прежний объективный балл не переводится в три оценки нового маршрута.':'Семантика балла требует уточнения.'}</p>}
 <details className="user-details"><summary>Версии проверки</summary><p>Программа: {assessment.blueprint_version||'не указана'} · маршрут: {assessment.path_version||'не указан'} · политика: {assessment.policy_version||'не указана'}</p>{stages&&<p>Кейс: {stages.work_sample.family} · {stages.work_sample.version||'версия не указана'}<br/>Рубрика: {stages.review.rubric_version||'версия не указана в паспорте'}</p>}<p className="muted">Баллы разных процедур и семейств не образуют общую шкалу сравнения.</p></details>
 </section>;
}

export function InteractionHistory({items}:{items:RecordData[]}){
 return <details className="user-details"><summary>История взаимодействия · {items.length}</summary><ol className="interaction-history">{items.map((item,index)=><li key={index}><strong>{statusLabels[item.status]||'Изменение статуса'}</strong><small>{prettyDate(item.at)} · {item.actor==='candidate'?'Кандидат':'Работодатель'}</small>{item.message&&<p>{item.message}</p>}</li>)}</ol></details>;
}

const fieldNames:Record<string,string>={name:'ФИО',title:'Должность',city:'Город',bio:'О себе',industry:'Отрасль',experience_years:'Стаж',salary_min:'Зарплатные ожидания',email:'Email',phone:'Телефон',telegram:'Telegram',description:'Описание',career_history:'Опыт работы',skills:'Навыки',soft_skills:'Софт-скиллы',declared_grade:'Предполагаемый уровень'};
function readable(value:unknown):string{
 if(value==null||value==='')return 'Не указано';
 if(Array.isArray(value))return value.map(readable).join(', ');
 if(typeof value==='object')return Object.values(value as RecordData).map(readable).join(' · ');
 return String(value);
}
export function ReadableFields({value,title}:{value:RecordData;title:string}){
 return <details className="user-details"><summary>{title}</summary><dl className="facts">{Object.entries(value||{}).filter(([key])=>key in fieldNames).map(([key,entry])=><div key={key}><dt>{fieldNames[key]}</dt><dd>{readable(entry)}</dd></div>)}</dl></details>;
}

export function selections(value:string):string[]{try{const parsed=JSON.parse(value||'[]');return Array.isArray(parsed)?parsed.filter(x=>typeof x==='string'):[];}catch{return [];}}
export function stored<T>(key:string,fallback:T):T{try{return JSON.parse(sessionStorage.getItem(key)||'null')??fallback;}catch{return fallback;}}
export function store(key:string,value:unknown){try{sessionStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}}
