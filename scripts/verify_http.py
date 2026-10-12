from pathlib import Path
import json,uuid,os,subprocess
import httpx,pandas as pd,numpy as np,joblib,sys
from feature_engineering import build_features
from model_core import predict

ROOT=Path(__file__).resolve().parents[1]
URL=os.getenv('VERIFY_URL','http://127.0.0.1:8792')
KEY=(ROOT/'secrets/model_api_key.txt').read_text().strip()
HEADERS={'Authorization':'Bearer '+KEY}

def main():
    results=[]
    def check(name,condition):
        results.append({'check':name,'pass':bool(condition)})
        assert condition,name
    with httpx.Client(base_url=URL,timeout=30) as c:
        base=json.loads((ROOT/'docs/example_request.json').read_text());base['request_id']=str(uuid.uuid4())
        check('health',c.get('/health/ready').status_code==200)
        check('auth_required',c.post('/v1/models/result_to_draw/predict',json=base).status_code==401)
        check('contract',c.get('/v1/models/result_to_draw/contract',headers=HEADERS).status_code==200)
        r=c.post('/v1/models/result_to_draw/predict',json=base,headers=HEADERS)
        check('predict_200',r.status_code==200)
        q=r.json();rid=q['request_id']
        check('correct_target',q['target']=='result_to_draw')
        check('finite_probability',0<=q['probability']<=1)
        check('result_persisted',c.get('/v1/models/result_to_draw/results/'+rid,headers=HEADERS).json()==q)
        check('idempotent_retry',c.post('/v1/models/result_to_draw/predict',json=base,headers=HEADERS).json()==q)
        changed=json.loads(json.dumps(base));changed['snapshot']['home_possession']=float(base['snapshot']['home_possession'] or 0)+1
        check('idempotency_conflict',c.post('/v1/models/result_to_draw/predict',json=changed,headers=HEADERS).status_code==409)
        s=base['snapshot'];frame=pd.DataFrame([s]);registry=json.loads((ROOT/'assets/manifest.json').read_text())
        x,_,_=build_features(frame,registry['predictors'])
        b=joblib.load(ROOT/'assets'/registry['models'][q['family']]['file'])
        raw,p=predict(b,x,frame.snapshot_minute)
        check('local_docker_parity',abs(q['probability']-p[0])<1e-8 and abs(q['raw_model_score']-raw[0])<1e-8)
        check('operational_gate',q['actionable_signal']==(q['signal'] and q['production_approved']))
        invalid=json.loads(json.dumps(base));invalid['request_id']=str(uuid.uuid4());invalid['snapshot'].pop('home_possession')
        check('missing_field_rejected',c.post('/v1/models/result_to_draw/predict',json=invalid,headers=HEADERS).status_code==422)
        invalid=json.loads(json.dumps(base));invalid['request_id']=str(uuid.uuid4());invalid['snapshot']['result_to_draw']=True
        check('future_target_rejected',c.post('/v1/models/result_to_draw/predict',json=invalid,headers=HEADERS).status_code==422)
        invalid=json.loads(json.dumps(base));invalid['request_id']=str(uuid.uuid4());invalid['snapshot']['dif_score']=42
        check('inconsistent_score_rejected',c.post('/v1/models/result_to_draw/predict',json=invalid,headers=HEADERS).status_code==422)
        outside=json.loads(json.dumps(base));outside['request_id']=str(uuid.uuid4());outside['snapshot']['state_developer_name']='FT'
        z=c.post('/v1/models/result_to_draw/predict',json=outside,headers=HEADERS).json()
        check('outside_horizon_recorded',z['eligible'] is False and z['probability'] is None and z['signal'] is False)
        for family in registry['models']:
            req={**base,'request_id':str(uuid.uuid4()),'family':family}
            check('family_'+family,c.post('/v1/models/result_to_draw/predict',json=req,headers=HEADERS).status_code==200)
    (ROOT/'outputs/v6_result_to_draw_20261011/http_validation.json').write_text(json.dumps({'checks':results,'persisted_request_id':rid,'result':q},indent=2),encoding='utf8')
    print('PASS',len(results),'HTTP checks; request',rid)

if __name__=='__main__':main()
