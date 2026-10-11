from .db import connection, dumps, password_hash, now, DEMO

def seed():
    if not DEMO:
        return
    candidates = [
        ("c_anna","Анна Смирнова","АС","backend","Backend разработчик","Екатеринбург",2,110000,
         ["python","fastapi","sql","postgresql","docker","git"],
         "Создаю API и сервисы на Python. В командных проектах отвечаю за backend и работу с данными. Ищу продуктовую команду.",True,"#a88bf0"),
        ("c_timur","Тимур Ахметов","ТА","frontend","Frontend разработчик","Казань",2,100000,
         ["react","typescript","javascript","html","git","testing"],
         "Разрабатываю доступные интерфейсы на React и TypeScript, пишу тесты. Участвую в продуктовом программировании.",True,"#5caaa5"),
        ("c_maria","Мария Волкова","МВ","ml","ML инженер","Москва",2,150000,
         ["python","pytorch","ml","pandas","sql","docker"],
         "Работаю с NLP и рекомендательными системами. Обучаю и оцениваю модели, превращаю эксперименты в API.",True,"#edac76"),
        ("c_ilya","Илья Соколов","ИС","backend",".NET разработчик","Пермь",3,140000,
         ["csharp","dotnet","sql","postgresql","docker","git"],
         "Разрабатываю сервисы на ASP.NET Core. Хорошо понимаю реляционные базы и проектирование API.",True,"#86a9da"),
        ("c_daria","Дарья Кузнецова","ДК","qa","Инженер по тестированию","Новосибирск",1,80000,
         ["testing","python","playwright","sql","git"],
         "Проверяю API и веб интерфейсы, пишу автоматические тесты. Ищу возможность выстроить контроль качества в продукте.",True,"#c88daf"),
        ("c_alex","Алексей Морозов","АМ","devops","DevOps инженер","Санкт-Петербург",3,180000,
         ["linux","docker","kubernetes","ci","python","network"],
         "Автоматизирую поставку сервисов, работаю с контейнерами и инфраструктурой. Люблю наблюдаемость и надёжность.",True,"#89936f"),
        ("c_sonya","Софья Орлова","СО","security","Специалист по безопасности","Томск",1,100000,
         ["security","network","linux","python","algorithms"],
         "Участвую в CTF, анализирую сетевой трафик и исследую уязвимости. Хочу применять навыки в защите реальных систем.",True,"#bd8872"),
        ("c_max","Максим Лебедев","МЛ","backend","Java разработчик","Москва",2,130000,
         ["java","spring","sql","postgresql","docker","git"],
         "Пишу сервисы на Java и Spring, проектирую API. Есть опыт командных проектов и работы с базами данных.",True,"#829aba"),
        ("c_lena","Елена Петрова","ЕП","design","Продуктовый дизайнер","Екатеринбург",1,75000,
         ["figma","ux","html","testing"],
         "Проектирую сценарии, прототипирую и проверяю интерфейсы на пользователях. В команде отвечала за UX и визуальный дизайн.",True,"#b4a168"),
        ("c_nikita","Никита Васильев","НВ","backend","Начинающий разработчик","Челябинск",0,65000,
         ["python","algorithms","git","sql"],
         "Изучаю Python и алгоритмы, делаю учебные проекты. Ищу стажировку с обратной связью и возможностью роста.",True,"#9a9bc6"),
        ("c_claims","Роман Беляев","РБ","backend","Python разработчик","Москва",2,110000,
         ["python","fastapi","sql","postgresql","docker","git"],
         "Разрабатываю API на Python, работаю с PostgreSQL и Docker. Все сведения о навыках пока указаны мной самостоятельно.",True,"#a8adba"),
        ("c_hidden","Павел Миронов","ПМ","frontend","Frontend разработчик","Казань",2,100000,
         ["react","typescript","html","git"],
         "Этот профиль скрыт и используется для проверки управления видимостью.",False,"#83918e"),
    ]
    vacancies = [
        ("v_python","org_sigma","Backend разработчик Python","СигмаТех","backend",["python","fastapi","postgresql","sql"],["docker","git"],120000,180000,1,"remote",
         "Создавать REST API на Python и FastAPI, работать с PostgreSQL и SQL. Опыт от одного года. Docker и Git будут плюсом."),
        ("v_front","org_sigma","Frontend разработчик React","СигмаТех","frontend",["react","typescript","html"],["testing","git"],110000,170000,1,"remote",
         "Разрабатывать веб интерфейсы на React и TypeScript. HTML и CSS обязательны. Тестирование и Git приветствуются."),
        ("v_ml","org_contour","ML инженер по рекомендациям","КонтурЛаб","ml",["python","ml","pytorch","pandas"],["sql","docker"],160000,220000,2,"hybrid",
         "Разрабатывать рекомендательные системы, обучать модели на PyTorch. Нужны Python, машинное обучение и Pandas. Работа в Москве."),
        ("v_dotnet","org_contour","Backend разработчик ASP.NET Core","КонтурЛаб","backend",["csharp","dotnet","postgresql"],["docker","sql"],140000,210000,2,"remote",
         "Создавать сервисы на C# и ASP.NET Core. PostgreSQL обязателен. Docker и SQL будут плюсом."),
        ("v_qa","org_sigma","Инженер автоматизации тестирования","СигмаТех","qa",["testing","python","playwright"],["sql","git"],90000,140000,1,"remote",
         "Писать автотесты API и интерфейсов на Python и Playwright. Знание SQL будет плюсом."),
        ("v_devops",None,"DevOps инженер","Демо · Облачные решения","devops",["linux","docker","kubernetes","ci"],["network","python"],180000,260000,2,"remote",
         "Поддерживать Linux инфраструктуру, Docker, Kubernetes и CI/CD. Автоматизировать развёртывание и наблюдаемость."),
        ("v_security",None,"Аналитик информационной безопасности","Демо · Безопасный контур","security",["security","network","linux"],["python"],110000,170000,1,"remote",
         "Анализировать сетевой трафик, расследовать инциденты и работать с Linux. Опыт CTF приветствуется."),
        ("v_java",None,"Java разработчик","Демо · Финансовые системы","backend",["java","spring","postgresql"],["docker","git"],130000,200000,1,"remote",
         "Разрабатывать сервисы на Java и Spring. Работать с PostgreSQL. JavaScript не требуется."),
        ("v_intern","org_sigma","Стажёр Python разработчик","СигмаТех","backend",["python","algorithms"],["sql","git"],60000,90000,0,"remote",
         "Учебные и продуктовые задачи на Python, алгоритмы и структуры данных. Коммерческий опыт не требуется. SQL будет плюсом."),
    ]
    # Synthetic contract fixtures. They describe individual task evidence, not inferred technology skills.
    events = [
        ("a_anna_api","c_anna","Командный API для городского сервиса","Продуктовое программирование","Финал · тестовый кейс","Backend разработка", "backend",["python","fastapi","postgresql"],"2 место","2026-09-18"),
        ("a_anna_algo","c_anna","Задачи на алгоритмы и структуры данных","Алгоритмическое программирование","Региональный этап · тестовый кейс","Участник",None,["algorithms"],"Финалист","2026-06-14"),
        ("a_timur","c_timur","Веб интерфейс и доступность","Продуктовое программирование","Финал · тестовый кейс","Frontend разработка","frontend",["react","typescript","html"],"2 место","2026-09-18"),
        ("a_maria","c_maria","Рекомендательная модель и оценка качества","Продуктовое программирование","Командный трек · тестовый кейс","ML инженер","ml",["python","ml","pytorch","pandas"],"1 место","2026-08-21"),
        ("a_ilya","c_ilya","Сервис обработки событий","Продуктовое программирование","Командный трек · тестовый кейс","Backend разработка","backend",["csharp","dotnet","postgresql"],"3 место","2026-07-09"),
        ("a_daria","c_daria","Автоматическая проверка веб сценариев","Продуктовое программирование","Командный трек · тестовый кейс","QA инженер","qa",["testing","python","playwright"],"Финалист","2026-09-02"),
        ("a_alex","c_alex","Сборка и развёртывание сервиса","Продуктовое программирование","Командный трек · тестовый кейс","DevOps инженер","devops",["linux","docker","ci"],"3 место","2026-08-12"),
        ("a_sonya","c_sonya","Анализ сетевого трафика","Программирование систем информационной безопасности","CTF · тестовый кейс","Участник","security",["security","network","linux"],"1 место","2026-09-11"),
        ("a_max","c_max","Обработка запросов и реляционные данные","Продуктовое программирование","Командный трек · тестовый кейс","Backend разработка","backend",["java","spring","postgresql"],"Финалист","2026-07-29"),
        ("a_lena","c_lena","Прототип и проверка пользовательских сценариев","Продуктовое программирование","Командный трек · тестовый кейс","Дизайнер","design",["figma","ux"],"2 место","2026-09-18"),
        ("a_nikita","c_nikita","Алгоритмические задачи","Алгоритмическое программирование","Региональный этап · тестовый кейс","Участник",None,["algorithms"],"Участник","2026-06-14"),
        ("a_hidden","c_hidden","Веб интерфейс","Продуктовое программирование","Тест видимости","Frontend разработка","frontend",["react","typescript"],"Участник","2026-09-01"),
    ]
    with connection() as conn:
        if conn.execute("SELECT 1 FROM meta WHERE key='seed_v1'").fetchone():
            return
        for id, name in [("org_sigma","СигмаТех"),("org_contour","КонтурЛаб")]:
            conn.execute("INSERT OR IGNORE INTO organizations VALUES(?,?)",(id,name))
        for id,name,initials,role,title,city,years,salary,skills,bio,visible,color in candidates:
            payload={"id":id,"name":name,"initials":initials,"role":role,"title":title,"city":city,
                     "experience_years":years,"salary_min":salary,"currency":"RUB","work_modes":["remote","hybrid"],
                     "skills":skills,"bio":bio,"color":color,"looking":True,"demo":True}
            conn.execute("INSERT OR IGNORE INTO candidates(id,payload,visible) VALUES(?,?,?)",(id,dumps(payload),visible))
        for id,org,title,company,role,required,optional,low,high,years,mode,description in vacancies:
            payload={"id":id,"title":title,"company":company,"role":role,"required_skills":required,
                     "optional_skills":optional,"salary_min":low,"salary_max":high,"currency":"RUB","salary_type":"net",
                     "experience_min":years,"work_mode":mode,"city":"Москва" if mode=="hybrid" else None,
                     "description":description,"source":"Платформа" if org else "Демо каталог","source_url":None,
                     "published_at":"2026-10-01","imported_at":now(),"demo":True}
            conn.execute("INSERT OR IGNORE INTO vacancies(id,org_id,payload) VALUES(?,?,?)",(id,org,dumps(payload)))
        for id,candidate,title,discipline,contest,role_label,role,skills,result,date in events:
            payload={"id":id,"candidate_id":candidate,"title":title,"discipline":discipline,"contest":contest,
                     "role_label":role_label,"role":role,"task_skills":skills,"result":result,"date":date,
                     "sports_rank":None,"individual_evidence":True,"source":"FSP mock adapter",
                     "source_id":"mock:"+id,"demo":True,"retrieved_at":now(),"verification":"demo_source",
                     "note":"Синтетическая запись для проверки интеграции. Не является реальным результатом ФСП."}
            conn.execute("INSERT OR IGNORE INTO achievements(id,candidate_id,payload) VALUES(?,?,?)",(id,candidate,dumps(payload)))
        users=[
            ("u_employer","hr@sigma.demo","Александр · СигмаТех","employer",None,"org_sigma"),
            ("u_employer2","hr@contour.demo","Ольга · КонтурЛаб","employer",None,"org_contour"),
            ("u_anna","anna@fsp.demo","Анна Смирнова","candidate","c_anna",None),
            ("u_timur","timur@fsp.demo","Тимур Ахметов","candidate","c_timur",None),
            ("u_admin","expert@fsp.demo","Эксперт ФСП","admin",None,None),
        ]
        hashed=password_hash("DemoPass2026!")
        for id,email,name,role,candidate,org in users:
            conn.execute("INSERT OR IGNORE INTO users(id,email,name,password,role,candidate_id,org_id) VALUES(?,?,?,?,?,?,?)",(id,email,name,hashed,role,candidate,org))
            conn.execute("UPDATE users SET email_verified=1,username=COALESCE(username,id),is_synthetic=1,admin_access=CASE WHEN role='admin' THEN 'operator' ELSE admin_access END WHERE id=?",(id,))
        conn.execute("INSERT INTO meta VALUES('seed_v1','1')")
