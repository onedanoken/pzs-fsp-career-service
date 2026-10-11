"""Owned competency blueprints; source excerpts are not republished."""
import json
from pathlib import Path
GRADES=['Intern','Junior','Middle','Senior']
SPECIALIZATIONS={
 'backend':{'label':'Backend Python','skills':['python','sql','fastapi'],'competencies':['data_processing','sql','debugging','api_contracts','tradeoffs']},
 'frontend':{'label':'Frontend TypeScript','skills':['typescript','javascript','react','testing'],'competencies':['state','data_processing','debugging','api_contracts','accessibility']},
 'qa':{'label':'QA','skills':['testing','sql','python'],'competencies':['test_design','sql','debugging','api_contracts','risk_analysis']},
 'devops':{'label':'DevOps','skills':['linux','docker','kubernetes','ci'],'competencies':['capacity','deployment','pipeline','observability','tradeoffs']},
 'ml':{'label':'ML и анализ данных','skills':['ml','pandas','python'],'competencies':['metrics','leakage','splitting','validation','tradeoffs']},
 'security':{'label':'Информационная безопасность','skills':['security','network','linux'],'competencies':['authorization','network','least_privilege','incident_response','tradeoffs']},
}
SOURCES=[
 {'id':'owasp-ssrf','url':'https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html','version':'living documentation','license':'CC BY-SA 4.0; metadata and owned summary only','summary':'Сетевые границы, проверка адресов и редиректов, защита от SSRF; собственные сценарии.'},
 {'id':'postgres-transactions','url':'https://www.postgresql.org/docs/16/mvcc-serialization-failure-handling.html','version':'PostgreSQL 16','license':'PostgreSQL License','summary':'Повтор целой транзакции при serialization_failure; самостоятельные сценарии конкуренции.'},
 {'id':'python-docs','url':'https://docs.python.org/3/tutorial/datastructures.html','version':'Python 3','license':'PSF License','summary':'Списки, фильтрация, множества и словари; собственные задачи обработки данных.'},
 {'id':'postgres-select','url':'https://www.postgresql.org/docs/current/sql-select.html','version':'current; fetched revision recorded separately','license':'PostgreSQL License','summary':'SELECT, WHERE, GROUP BY, агрегаты и порядок выдачи.'},
 {'id':'mdn-fetch','url':'https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch','version':'living documentation','license':'CC BY-SA 2.5; metadata and own summary only','summary':'Статусы HTTP, обработка ошибки, гонки клиентских запросов.'},
 {'id':'interview-handbook','url':'https://github.com/yangshun/tech-interview-handbook','version':'main; pin exact revision on ingestion','license':'MIT','summary':'Форматы интервью и разбор решений; внешние ссылки отдельно не импортируются.'},
 {'id':'k8s-deploy','url':'https://kubernetes.io/docs/concepts/workloads/controllers/deployment/','version':'living; fetched hash recorded','license':'CC BY 4.0; metadata and owned summary only','summary':'Deployment, готовность реплик, безопасный rollout и ресурсный бюджет.'},
 {'id':'sklearn-metrics','url':'https://scikit-learn.org/stable/modules/model_evaluation.html','version':'stable; fetched hash recorded','license':'BSD 3-Clause; metadata and owned summary only','summary':'Матрица ошибок, precision, recall, F1 и независимая проверка модели.'},
 {'id':'owasp-authorization','url':'https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html','version':'living; fetched hash recorded','license':'CC BY-SA 4.0; metadata and owned summary only','summary':'Проверка прав на каждый объект, запрет по умолчанию и минимальные привилегии.'},
 {'id':'k8s-resources','url':'https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/','version':'living; fetched hash recorded','license':'CC BY 4.0; metadata and owned summary only','summary':'Размещение по requests CPU и памяти; собственные ограниченные сценарии без init-контейнеров и overhead.'},
 {'id':'k8s-probes','url':'https://kubernetes.io/docs/concepts/workloads/pods/probes/','version':'living; fetched hash recorded','license':'CC BY 4.0; metadata and owned summary only','summary':'Различие readiness, liveness и корректности бизнес-ответа.'},
 {'id':'sklearn-pitfalls','url':'https://scikit-learn.org/stable/common_pitfalls.html','version':'stable; fetched hash recorded','license':'BSD 3-Clause; metadata and owned summary only','summary':'Обучение preprocessing на train, данные будущего и границы независимой оценки.'},
]
BLUEPRINT_VERSION='competencies-2026-10-v14'
from .content_registry import sources as content_sources
SOURCES.extend(content_sources())
SOURCES.append({'id':'w3c-dialog-pattern','url':'https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/','version':'APG living reference; metadata only','license':'W3C Software and Document License; own questions only','summary':'Keyboard focus, modal lifecycle and accessible status in own bounded scenarios.'})
# Draft thresholds require a human pilot before being represented as calibrated.
POLICY={'pass_score':70,'strong_score':90,'change_days':90,'attempt_cooldown_hours':24,'calibration':'pilot_unvalidated','version':'grade-policy-v1'}


def source_snapshot():
    """Freeze permitted provenance with an issued session, never remote content."""
    root=Path(__file__).resolve().parents[1]
    manifest=root/'resources/knowledge_manifest.json'
    if not manifest.exists():manifest=root/'data/knowledge_manifest.json'
    recorded={}
    if manifest.exists() and manifest.stat().st_size<=1024*1024:
        try:recorded={s['id']:s for s in json.loads(manifest.read_text(encoding='utf-8'))['sources']}
        except (KeyError,TypeError,ValueError):pass
    for item in content_sources():
        if 'sha256' in item:recorded[item['id']]=item
    result=[]
    for source in SOURCES:
        row={k:source[k] for k in ['id','url','version','license','summary']}
        saved=recorded.get(source['id'],{})
        if saved.get('url')==source['url']:
            row.update({k:saved[k] for k in ['sha256','upstream_revision','fetched_at'] if k in saved})
        row['snapshot_available']='sha256' in row
        result.append(row)
    return result
