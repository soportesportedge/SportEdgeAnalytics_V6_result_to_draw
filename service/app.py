"""Target-specific inference with durable, idempotent results."""
import os, json, hashlib, hmac, sqlite3, math, uuid, asyncio
from pathlib import Path
from datetime import datetime,timezone
from functools import lru_cache
from contextlib import asynccontextmanager
import joblib
import pandas as pd
import numpy as np
from fastapi import FastAPI,HTTPException,Header
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool
from threadpoolctl import threadpool_limits
from feature_engineering import build_features
from model_core import predict

ASSETS=Path(os.getenv('MODEL_ASSETS','assets'))
DB=Path(os.getenv('RESULTS_DB','data/result_to_draw.sqlite'))
REGISTRY=json.loads((ASSETS/'manifest.json').read_text(encoding='utf8'))
LOCK=asyncio.Semaphore(1)

def key():
    file=os.getenv('MODEL_API_KEY_FILE')
    return Path(file).read_text().strip() if file else os.getenv('MODEL_API_KEY','')

def authorize(value):
    expected=key()
    if not expected:raise HTTPException(503,'API key not configured')
    if not value or not hmac.compare_digest(value,'Bearer '+expected):raise HTTPException(401,'Unauthorized')

def connection():
    conn=sqlite3.connect(DB,timeout=5)
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA synchronous=FULL')
    return conn

@lru_cache(maxsize=8)
def bundle(family):
    item=REGISTRY['models'].get(family)
    if item is None:raise HTTPException(422,'Unknown model family')
    p=ASSETS/item['file']
    if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise RuntimeError('Artifact checksum mismatch')
    return joblib.load(p)

@asynccontextmanager
async def lifespan(app):
    DB.parent.mkdir(parents=True,exist_ok=True)
    with connection() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS model_results (request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, target TEXT NOT NULL, model_version TEXT NOT NULL, family TEXT NOT NULL, fixture_id INTEGER NOT NULL, captured_at TEXT NOT NULL, created_at TEXT NOT NULL, input_json TEXT NOT NULL, result_json TEXT NOT NULL)')
        c.execute('CREATE INDEX IF NOT EXISTS result_fixture_time ON model_results(fixture_id,captured_at)')
    bundle(REGISTRY['selected_family'])
    yield

app=FastAPI(title='SportEdge V6 result_to_draw',version='1.0.0',lifespan=lifespan)

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:uuid.UUID=Field(default_factory=uuid.uuid4)
    family:str=REGISTRY['selected_family']
    snapshot:dict

def validate(s):
    required=REGISTRY['predictors']+['id','captured_at','state_developer_name']
    missing=[c for c in required if c not in s]
    if missing:raise HTTPException(422,{'missing_fields':missing,'null_allowed_for_predictors':True})
    targets={'result_to_draw','home_score_to_increase','away_score_to_increase','home_result_to_win','away_result_to_win','both_teams_to_score'}
    if any(s.get(c) is not None for c in targets):raise HTTPException(422,'Future labels must be absent or null')
    try:
        stamp=pd.Timestamp(s['captured_at'])
        if pd.isna(stamp) or stamp.tzinfo is None:raise ValueError()
        if isinstance(s['id'],bool) or int(s['id'])<1 or float(s['id'])!=int(s['id']):raise ValueError()
        minute=float(s['snapshot_minute'])
        if isinstance(s['snapshot_minute'],bool) or not math.isfinite(minute) or not 0<=minute<=200:raise ValueError()
        for c in ['home_score','away_score']:
            v=float(s[c])
            if isinstance(s[c],bool) or not math.isfinite(v) or v<0 or not v.is_integer():raise ValueError()
        if float(s['dif_score'])!=float(s['home_score'])-float(s['away_score']):raise ValueError()
        for c in REGISTRY['predictors']:
            v=s[c]
            if v is None or c in ('league_id','cluster'):continue
            if isinstance(v,str) and v.lower() in ('t','f','true','false'):continue
            if not math.isfinite(float(v)):raise ValueError()
        json.dumps(s,allow_nan=False)
    except (ValueError,TypeError,OverflowError):raise HTTPException(422,'Invalid finite numbers, score consistency, fixture or timezone timestamp')

def infer(req):
    validate(req.snapshot)
    b=bundle(req.family);s=req.snapshot
    request_body={'family':req.family,'snapshot':s}
    body=json.dumps(request_body,sort_keys=True,allow_nan=False,separators=(',',':'))
    digest=hashlib.sha256(body.encode()).hexdigest();rid=str(req.request_id)
    with connection() as c:
        previous=c.execute('SELECT request_hash,result_json FROM model_results WHERE request_id=?',(rid,)).fetchone()
        if previous:
            if previous[0]!=digest:raise HTTPException(409,'request_id already used for different input')
            return json.loads(previous[1])
    eligible=s['state_developer_name']=='INPLAY_2ND_HALF' and float(s['snapshot_minute'])>=70 and abs(float(s['dif_score']))==1
    raw=probability=None
    if eligible:
        frame=pd.DataFrame([s]);x,_,_=build_features(frame,REGISTRY['predictors'])
        with threadpool_limits(limits=1):rr,pp=predict(b,x,frame.snapshot_minute)
        raw,probability=float(rr[0]),float(pp[0])
    threshold=float(b['threshold']);signal=bool(eligible and probability>=threshold)
    approved=bool(REGISTRY['models'][req.family]['production_approved'])
    result={'request_id':rid,'target':'result_to_draw','fixture_id':int(s['id']),'captured_at':s['captured_at'],'created_at':datetime.now(timezone.utc).isoformat(),'family':req.family,'candidate_id':b['config']['candidate_id'],'model_version':b['model_version'],'artifact_sha256':REGISTRY['models'][req.family]['sha256'],'raw_model_score':raw,'probability':probability,'threshold':threshold,'eligible':eligible,'signal':signal,'actionable_signal':bool(approved and signal),'research_only':not approved,'production_approved':approved,'calibration_method':'component_calibrations' if b.get('ensemble') else b['calibrator'].method,'decision_policy':b['policy'],'target_definition':REGISTRY['target_definition']}
    with connection() as c:
        c.execute('INSERT INTO model_results VALUES (?,?,?,?,?,?,?,?,?,?)',(rid,digest,'result_to_draw',b['model_version'],req.family,int(s['id']),str(s['captured_at']),result['created_at'],json.dumps(s,allow_nan=False),json.dumps(result,allow_nan=False)))
    return result

@app.get('/health/ready')
def ready():
    with connection() as c:c.execute('SELECT 1').fetchone()
    return {'status':'ready','target':'result_to_draw','selected_family':REGISTRY['selected_family'],'production_approved':REGISTRY['models'][REGISTRY['selected_family']]['production_approved']}

@app.post('/v1/models/result_to_draw/predict')
async def prediction(req:Request,authorization:str|None=Header(default=None)):
    authorize(authorization)
    async with LOCK:return await run_in_threadpool(infer,req)

@app.get('/v1/models/result_to_draw/results/{request_id}')
def result(request_id:uuid.UUID,authorization:str|None=Header(default=None)):
    authorize(authorization)
    with connection() as c:r=c.execute('SELECT result_json FROM model_results WHERE request_id=?',(str(request_id),)).fetchone()
    if not r:raise HTTPException(404,'Result not found')
    return json.loads(r[0])

@app.get('/v1/models/result_to_draw/contract')
def contract(authorization:str|None=Header(default=None)):
    authorize(authorization)
    return {k:REGISTRY[k] for k in ['target','target_definition','predictors','selected_family','models']}
