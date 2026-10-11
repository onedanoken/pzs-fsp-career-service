"""Answer execution uses disposable, resource-bounded SQL processes.

Python submissions are unavailable until a separately audited runner is deployed.
No untrusted Python executes in the API or through the host Docker socket.
"""
import json,os,subprocess,sys
from fastapi import HTTPException

def python_check(code,tests):
    raise HTTPException(503,'Исполнитель Python не подключён. Ответ сохранён; попытка не завершена.')

def sql_check(query,rows,expected):
    if len(query)>4000:return {'passed':0,'total':1,'error':'Слишком длинный SQL'}
    runner=os.path.join(os.path.dirname(__file__),'sql_runner.py')
    try:
        result=subprocess.run([sys.executable,'-I',runner],input=json.dumps({'query':query,'rows':rows}),text=True,capture_output=True,timeout=3)
        output=json.loads(result.stdout)
        correct=output.get('ok') and sorted(map(tuple,output['rows']),key=str)==sorted(map(tuple,expected),key=str)
        return {'passed':int(bool(correct)),'total':1,'error':None if correct else 'Результат не совпал с проверкой'}
    except (subprocess.TimeoutExpired,ValueError,OSError):return {'passed':0,'total':1,'error':'SQL не завершился в пределах лимитов'}
