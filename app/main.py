from contextlib import asynccontextmanager
from importlib import import_module
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, META, MODULES, DB_PATH
from app.errors import BusinessError
from app.engine import evaluate
from app.release import release_payload, business_day
from app import store
from app.archive import serialize

@asynccontextmanager
async def lifespan(app):
    store.save(DB_PATH,evaluate())
    yield
app=FastAPI(title=META['title'],lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(ROOT/'app/static')),name='static')
@app.exception_handler(BusinessError)
async def business_error(request:Request,error:BusinessError):
    return JSONResponse({'code':error.code,'detail':error.message},status_code=error.status)
@app.get('/')
def home():
    return RedirectResponse('/'+MODULES[0],status_code=303)
@app.get('/healthz')
def health():
    return {'status':'ok','project':META['short']}
@app.post('/api/runs')
def create(payload:dict=Body(default={})):
    return store.save(DB_PATH,evaluate(payload))
@app.post('/api/runs/{run_id}/rerun')
def rerun(run_id:str):
    previous=store.get(DB_PATH,run_id)
    if 'input_snapshot' not in previous:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    return store.save(DB_PATH,evaluate({'dataset':previous['source']},snapshot=previous['input_snapshot']))
@app.get('/api/runs')
def runs():
    return store.history(DB_PATH)
@app.get('/api/runs/{run_id}')
def run(run_id:str):
    return store.get(DB_PATH,run_id)
@app.post('/api/runs/{run_id}/review')
def review(run_id:str,payload:dict=Body(...)):
    revision=payload.get('revision')
    if not isinstance(revision,int) or revision<1: raise BusinessError('BAD_REVISION','版本号须为正整数')
    return store.review(DB_PATH,run_id,revision)
@app.post('/api/runs/{run_id}/publish')
def publish(run_id:str,payload:dict=Body(default={})):
    result=store.get(DB_PATH,run_id)
    gate=result.get('publish')
    if not gate:
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成对账')
    revision=payload.get('revision')
    if revision is not None and revision!=result['revision']:
        raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    if not gate['publishable']:
        raise BusinessError('RELEASE_BLOCKED','余额对账未通过，禁止发布',409)
    if gate['day']!=business_day(result['context']['as_of']):
        raise BusinessError('BAD_TIME','结算日不一致')
    return store.publish(DB_PATH,result,release_payload(result))
@app.get('/api/releases/{day}')
def releases(day:str):
    return {'day':day,'releases':store.releases(DB_PATH,day)}
@app.get('/api/releases/{day}/active')
def active_release(day:str):
    return store.release(DB_PATH,day)
@app.get('/api/runs/{run_id}/release')
def run_release(run_id:str):
    result=store.get(DB_PATH,run_id)
    return store.release(DB_PATH,business_day(result['context']['as_of']))
@app.get('/api/export/{run_id}')
def export(run_id:str):
    result=store.get(DB_PATH,run_id)
    day=(result.get('publish') or {}).get('day')
    release=None
    if day:
        try: release=store.release(DB_PATH,day)
        except BusinessError: release=None
    return Response(serialize(result,release),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{run_id}.zip"'})
for key in MODULES:
    app.include_router(import_module(f'app.modules.{key}.handler').router)
