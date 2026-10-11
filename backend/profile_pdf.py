"""Standardized PDF uses the same authorized representation as the API."""
import io,threading
from html import escape
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from .auth import session_user,require_role,require_verified
from .db import ROOT
from .domain import get_candidate

router=APIRouter(prefix='/api',tags=['Standardized profile documents'])
lock=threading.Lock()
def render(profile):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor
    font=ROOT/'frontend'/'dist'/'assets'/'brand'/'JetBrainsMono-Regular.ttf'
    if not font.exists():font=ROOT/'frontend'/'public'/'assets'/'brand'/'JetBrainsMono-Regular.ttf'
    if not font.exists():raise HTTPException(503,'Шрифт профиля отсутствует; выполните сборку интерфейса')
    with lock:
        if 'FSPMono' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('FSPMono',str(font)))
    output=io.BytesIO();doc=SimpleDocTemplate(output,pagesize=(595,842),rightMargin=44,leftMargin=44,topMargin=40,bottomMargin=45,title='ФСП Карьера: профиль',author='ФСП Карьера')
    heading=ParagraphStyle('Heading',fontName='FSPMono',fontSize=19,leading=26,textColor=HexColor('#402FFF'),spaceAfter=15)
    text=ParagraphStyle('Text',fontName='FSPMono',fontSize=9,leading=15,spaceAfter=10)
    elements=[Paragraph('ФСП Карьера',heading),Paragraph(escape(profile['name']),heading),Paragraph(escape(profile['title']),text)]
    def line(label,value):
        if value is not None and value!='':elements.append(Paragraph(escape(label+': '+str(value)),text))
    line('Город',profile.get('city'));line('Опыт, лет',profile.get('experience_years'))
    line('Навыки',', '.join(profile.get('skill_labels',{}).values()))
    line('Софт-скиллы',', '.join(profile.get('soft_skills',[])))
    line('О себе',profile.get('bio',''))
    for category in profile.get('categories',[]):
        line('Категория',category['label']+'; результат '+str(category['score'])+'%; '+('синтетический пример' if category['status']=='demo' else 'предварительная шкала, калибровка не завершена'))
    if profile.get('contacts'):line('Разрешённые контакты',', '.join(profile['contacts'].values()))
    else:line('Контакты','доступны после принятия приглашения')
    for role in profile.get('career_history',[]):line('Опыт работы','; '.join(str(v) for v in role.values()))
    for proof in profile.get('achievements',[]):
        if proof['active']:line('Источник ФСП',proof['title']+'; '+proof.get('date','')+'; '+proof.get('result','')+(' (demo)' if proof.get('demo') else ''))
    line('Версия профиля',profile['version'])
    def footer(canvas,document):
        canvas.setFont('FSPMono',8);canvas.drawString(44,26,'ФСП Карьера · Страница '+str(document.page))
    doc.build(elements,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()

def document(profile):return Response(render(profile),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="fsp-profile.pdf"','Cache-Control':'no-store'})
@router.get('/profile/pdf')
def own_pdf(user=Depends(session_user)):
    require_role(user,'candidate');return document(get_candidate(user['candidate_id'],user))
@router.get('/candidates/{identity}/pdf')
def candidate_pdf(identity:str,user=Depends(session_user)):
    require_role(user,'employer','candidate');require_verified(user)
    return document(get_candidate(identity,user))
