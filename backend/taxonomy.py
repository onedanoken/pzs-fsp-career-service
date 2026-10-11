import re

SKILLS = {
    "python": ("Python", ["python", "питон", "пайтон"]),
    "fastapi": ("FastAPI", ["fastapi", "fast api"]),
    "django": ("Django", ["django", "джанго"]),
    "sql": ("SQL", ["sql", "реляционные базы", "язык запросов"]),
    "postgresql": ("PostgreSQL", ["postgresql", "postgres", "постгрес"]),
    "docker": ("Docker", ["docker", "контейнеризация", "докер"]),
    "git": ("Git", ["git", "гит"]),
    "react": ("React", ["react", "реакт"]),
    "typescript": ("TypeScript", ["typescript", "тайпскрипт", "ts"]),
    "javascript": ("JavaScript", ["javascript", "js", "джаваскрипт"]),
    "html": ("HTML / CSS", ["html", "css", "верстка", "вёрстка"]),
    "csharp": ("C#", ["c#", "c sharp", "csharp", "си шарп"]),
    "dotnet": (".NET", [".net", "asp.net", "dotnet"]),
    "java": ("Java", ["java", "джава"]),
    "spring": ("Spring", ["spring", "спринг"]),
    "pytorch": ("PyTorch", ["pytorch", "torch"]),
    "ml": ("Machine learning", ["machine learning", "машинное обучение", "ml"]),
    "pandas": ("Pandas", ["pandas", "пандас"]),
    "algorithms": ("Алгоритмы", ["алгоритмы", "алгоритмический", "структуры данных"]),
    "testing": ("Тестирование", ["тестирование", "pytest", "автотесты", "qa"]),
    "playwright": ("Playwright", ["playwright"]),
    "linux": ("Linux", ["linux", "линукс"]),
    "kubernetes": ("Kubernetes", ["kubernetes", "k8s", "кубернетес"]),
    "ci": ("CI / CD", ["ci/cd", "ci cd", "gitlab ci", "github actions"]),
    "security": ("Информационная безопасность", ["кибербезопасность", "информационная безопасность", "security", "ctf"]),
    "network": ("Сетевой анализ", ["сети", "сетевой анализ", "wireshark", "анализ трафика"]),
    "figma": ("Figma", ["figma", "фигма"]),
    "ux": ("UX / UI", ["ux", "ui", "интерфейсы", "продуктовый дизайн"]),
}
ROLES = {
    "backend": "Backend разработка", "frontend": "Frontend разработка",
    "ml": "ML и анализ данных", "devops": "DevOps и инфраструктура",
    "qa": "Тестирование", "security": "Информационная безопасность", "design": "Продуктовый дизайн",
}
ROLE_SKILLS = {
    "backend": {"python","fastapi","django","dotnet","csharp","java","spring","sql"},
    "frontend": {"react","typescript","javascript","html"},
    "ml": {"ml","pytorch","pandas","python"},
    "devops": {"docker","linux","kubernetes","ci"},
    "qa": {"testing","playwright"},
    "security": {"security","network","linux"},
    "design": {"figma","ux"},
}

def canonical(skill):
    if skill in SKILLS:
        return skill
    lower = skill.lower().strip()
    for key, (name, aliases) in SKILLS.items():
        if lower == name.lower() or lower in aliases:
            return key
    return None

def skill_mentions(text):
    """Every mention, including negations; longest aliases win at each position."""
    mentions=[]
    for key,(name,aliases) in SKILLS.items():
        occupied=[]
        for alias in sorted(aliases,key=len,reverse=True):
            for match in re.finditer(r'(?<![\w])'+re.escape(alias)+r'(?![\w])',text,re.IGNORECASE):
                if any(match.start()<end and match.end()>start for start,end in occupied):continue
                occupied.append((match.start(),match.end()))
                before=re.split(r'[;!?\n]|\.(?=\s|$)',text[:match.start()].lower())[-1][-90:]
                after=re.split(r'[;!?\n,]|\.(?=\s|$)',text[match.end():].lower())[0][:90]
                local_before=re.split(r'[,]',before)[-1]
                negated=bool(re.search(r'(?:не\s+(?:требуется|требуются|нужен|нужна|нужно|используем|знаю|изучал|использовал|владею)|без\s+(?:опыта\s+(?:работы\s+)?(?:с\s+)?)?)\s*(?:знание\s+)?$',local_before)
                             or re.match(r'\s+(?:не\s+(?:требуется|требуются|нужен|нужна|нужно|используем|знаю|изучал|использовал)|не\s+владею)\b',after))
                optional=bool(re.search(r'желательн|плюсом|необязательн|приветству',before+' '+after))
                mentions.append({'skill':key,'name':name,'span':match.group(),'start':match.start(),'end':match.end(),
                                 'required':not optional,'negated':negated})
    return sorted(mentions,key=lambda item:(item['start'],item['end']))

def extract_skills(text):
    mentions=skill_mentions(text)
    result=[]
    for key in dict.fromkeys(item['skill'] for item in mentions):
        group=[m for m in mentions if m['skill']==key]
        positive=[m for m in group if not m['negated']]
        chosen=dict(next((m for m in positive if m['required']),positive[0] if positive else group[0]))
        chosen['conflicting_mentions']=bool(positive and len(positive)!=len(group))
        result.append(chosen)
    return sorted(result,key=lambda item:item['start'])

def _legacy_extract_skills(text):
    """Deterministic extraction with spans, rather than generated facts."""
    result = []
    for key, (name, aliases) in SKILLS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            # Match punctuation-heavy technology names without matching Java inside JavaScript.
            pattern = r"(?<![\w])" + re.escape(alias) + r"(?![\w])"
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                left = max(0, match.start()-70)
                right = min(len(text), match.end()+90)
                before = re.split(r"[.;!?\n]",text[:match.start()].lower())[-1]
                after = re.split(r"[.;!?\n]",text[match.end():].lower())[0]
                negated = bool(re.search(r"не\s+(?:требуется|требуются|нужен|нужна|нужно|используем)\s*(?:знание\s+)?$",before)
                               or re.match(r"\s+не\s+(?:требуется|требуются|нужен|нужна|нужно|используем)\b",after))
                optional = bool(re.search(r"желательн|плюсом|необязательн|приветству",before+" "+after))
                result.append({"skill":key,"name":name,"span":text[match.start():match.end()],
                               "start":match.start(),"end":match.end(),"required":not optional,"negated":negated})
                break
    return sorted(result, key=lambda x:x["start"])

def normalize_text(text):
    normalized = text.lower()
    for item in extract_skills(text):
        if not item["negated"]:
            normalized += " " + item["skill"] + " " + item["name"].lower()
    return normalized

def categories(candidate, achievements):
    active = [a for a in achievements if a["active"]]
    found = {}
    for a in active:
        role = a.get("role")
        if role in ROLES:
            if role not in found:
                found[role] = {"id":role,"label":ROLES[role],"basis":"source_demo" if a.get("demo") else "source",
                               "evidence_ids":[]}
            found[role]["evidence_ids"].append(a["id"])
            if not a.get("demo"):
                found[role]["basis"]="source"
    claimed = set(candidate.get("skills", []))
    for role, skills in ROLE_SKILLS.items():
        if role not in found and len(skills & claimed) >= 2:
            found[role] = {"id":role,"label":ROLES[role],"basis":"claimed","evidence_ids":[]}
    role = candidate.get("role")
    if role in ROLES and role not in found:
        found[role] = {"id":role,"label":ROLES[role],"basis":"claimed","evidence_ids":[]}
    return list(found.values())
