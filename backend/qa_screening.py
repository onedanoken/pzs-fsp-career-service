"""Owned QA screening cases. No Python/SQL answer is required.

Rules are provided in the task, so correctness comes from the stated contract.
This is a bounded pilot, not evidence of full professional qualification.
"""
import hashlib,json,secrets
from .assessment_catalog import BLUEPRINT_VERSION,source_snapshot,GRADES

VERSION='qa-screening-2026-v1'
GROUPS={
 'test_design':['boundaries','decision_table','metamorphic'],
 'api_contracts':['validation','idempotency','authorization'],
 'debugging':['reproduction','regression','environment'],
}

def scenario(family,grade,rng):
 level=GRADES.index(grade);product=rng.choice(['сервис записи на тренировку','кабинет участника турнира','система бронирования консультаций'])
 context={'family':family,'grade':grade,'product':product,'level':level}
 if family=='boundaries':
  low=rng.choice([14,18,21]);high=rng.choice([55,60,65]);context.update(low=low,high=high)
  prompt=f'QA проверяет {product}. Допустимый возраст — от {low} до {high} включительно. Выберите пары, проверяющие переход между допустимым и недопустимым значением на каждой границе.'
  options=[(True,f'{low-1} и {low}'),(True,f'{high} и {high+1}'),(False,f'{low+2} и {high-2}'),(False,f'{low+4} и {high-4}')]
  if level>=2:
   prompt+=' Поле обязательное; пустой ввод также должен быть отклонён. Добавьте соответствующую проверку.'
   options.append((True,'Пустой ввод'));context['required']=True
  kind='multi'
 elif family=='decision_table':
  limit=rng.choice([2,3,5]);context['seat_limit']=limit
  prompt=f'Для {product} заявка подтверждается только при наличии согласия и хотя бы одного свободного места. Выберите случаи с ожидаемым подтверждением.'
  options=[(True,f'Согласие есть, свободных мест {limit}'),(False,'Согласия нет, свободных мест 1'),(False,'Согласие есть, свободных мест 0'),(False,'Нет согласия и свободных мест')]
  if level>=2:
   prompt+=' В Middle/Senior дополнительно требуется действующая регистрация.'
   options=[(True,f'Согласие есть, свободных мест {limit}, регистрация действует'),(False,'Согласие есть, свободных мест 1, регистрация отозвана'),(False,'Согласия нет, свободных мест 1, регистрация действует'),(False,'Согласие есть, мест 0, регистрация действует')]
  kind='multi'
 elif family=='metamorphic':
  prompt=f'QA проверяет поиск в продукте «{product}». По контракту порядок и повторение одинаковых тегов не меняют множество найденных записей. Какие преобразования должны сохранить результат?'
  options=[(True,'Переставить теги'),(True,'Добавить повтор уже выбранного тега'),(False,'Добавить новый обязательный тег'),(False,'Удалить все фильтры')]
  if level>=2:prompt+=' Пробелы по краям каждого тега игнорируются.';options.append((True,'Добавить пробелы по краям тегов'))
  kind='multi'
 elif family=='validation':
  field=rng.choice(['имя','город','название команды']);context['field']=field
  prompt=f'По контракту API продукта «{product}» отсутствие обязательного поля «{field}» даёт 422 и не создаёт запись. Какой тест подтверждает контракт полностью?'
  options=[(True,'Отправить запрос без поля, проверить 422 и отсутствие новой записи'),(False,'Проверить только цвет предупреждения в интерфейсе'),(False,'Проверить только любой ответ сервера'),(False,'Отправить корректные данные и проверить 200')]
  if level>=2:prompt+=' Неуспешный ответ не должен раскрывать введённый пароль.';options[0]=(True,'Отправить запрос без поля, проверить 422, отсутствие записи и отсутствие пароля в ответе')
  kind='choice'
 elif family=='idempotency':
  prompt=f'QA проверяет создание заявки в продукте «{product}». Первый ответ потерялся. Повтор с тем же ключом и теми же данными обязан вернуть прежнюю заявку. Какую проверку нужно выполнить?'
  options=[(True,'Повторить тот же ключ и данные, сравнить ID и убедиться, что заявка одна'),(False,'Использовать новый ключ и проверить только 200'),(False,'Повторить запрос, не проверяя состояние хранилища'),(False,'Считать отсутствие ошибки в браузере достаточным')]
  if level>=2:prompt+=' Тот же ключ с изменённым содержимым должен вернуть 409 без новой заявки.';options[0]=(True,'Проверить ID и одну заявку при повторе; изменённые данные с тем же ключом дают 409 без новой записи')
  kind='choice'
 elif family=='authorization':
  prompt=f'По контракту {product} участник видит только собственные заявки. Какие случаи обязательны для проверки доступа?'
  options=[(True,'Своя заявка доступна'),(True,'Заявка другого участника недоступна'),(True,'Запрос без входа отклонён'),(False,'Все участники видят чужие заявки, если знают ID')]
  if level>=2:prompt+=' После отзыва доступа следующий запрос также должен быть отклонён.';options.append((True,'Проверить заявку после отзыва доступа'))
  kind='multi'
 elif family=='reproduction':
  code=rng.choice(['409','422','503']);context['response']=code
  prompt=f'В {product} тестировщик получил HTTP {code} вместо ожидаемого ответа. Что нужно приложить к дефекту, чтобы другой человек мог воспроизвести проблему?'
  options=[(True,'Версию, предусловия, шаги, обезличенный запрос, ожидание и фактический ответ'),(False,'Только фразу «не работает»'),(False,'Пароль участника и рабочий токен'),(False,'Предположение о виновном разработчике')]
  kind='choice'
 elif family=='regression':
  prompt=f'В {product} исправили двойное создание заявки при повторном нажатии. Выберите проверки, которые покрывают исправление и соседний основной сценарий.'
  options=[(True,'Одно нажатие создаёт одну заявку'),(True,'Двойное нажатие создаёт одну заявку'),(True,'Повтор после потери ответа возвращает ту же заявку'),(False,'Проверить только новый текст кнопки')]
  if level>=2:prompt+=' Обработка общая для двух пользователей; их разные заявки не должны объединяться.';options.append((True,'Два разных участника создают разные заявки'))
  kind='multi'
 elif family=='environment':
  prompt=f'В {product} один автотест получает 503, остальные проходят. В контракте 503 означает временную недоступность внешнего сервиса. Какое первое действие помогает отличить дефект продукта от проблемы окружения?'
  options=[(True,'Сопоставить версию, состояние зависимости, обезличенные логи и повторяемость на контролируемом окружении'),(False,'Автоматически объявить все тесты успешными'),(False,'Удалить тест из набора'),(False,'Перезапустить рабочую базу без проверки причины')]
  kind='choice'
 else:raise ValueError('Unknown QA family')
 if level==3:
  prompt+=' Проверка выполняется на изолированных данных; нельзя исправлять рабочую базу или публиковать персональные сведения.'
 return prompt,kind,options,context

def generate(grade,rng,seed):
 first=[rng.choice(families) for families in GROUPS.values()];rng.shuffle(first)
 remaining=[f for group in GROUPS.values() for f in group if f not in first];rng.shuffle(remaining)
 families=first+remaining[:3];items=[];signatures=[]
 for family in families:
  competency=next(k for k,v in GROUPS.items() if family in v)
  prompt,kind,labels,condition=scenario(family,grade,rng);signatures.append(condition)
  labels=list(labels);rng.shuffle(labels);choices=[];expected=[]
  for correct,text in labels:
   identity=secrets.token_hex(5);choices.append({'id':identity,'text':text})
   if correct:expected.append(identity)
  item={'id':'item_'+secrets.token_hex(8),'kind':kind,'competency':competency,'family':'qa:'+family,'version':VERSION,'prompt':prompt,'choices':choices,'origin':'owned_qa_contract_case','private':{'expected':expected[0] if kind=='choice' else expected,'condition':condition}}
  item['sha256']=hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False).encode()).hexdigest();items.append(item)
 return {'items':items,'condition_signature':hashlib.sha256(json.dumps(signatures,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'seed':str(seed),'blueprint_version':BLUEPRINT_VERSION,'generator':'qa_contract_cases','neural_items':0,'total_items':len(items),'sources':['istqb-reference','qa-interview-bank'],'sources_snapshot':source_snapshot(),'tested_skills':['testing'],'calibration':'pilot_unvalidated','limits':{'duration_minutes':45}}
