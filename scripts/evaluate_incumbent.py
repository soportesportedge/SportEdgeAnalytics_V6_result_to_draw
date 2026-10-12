"""Re-evaluate the verified incumbent on the new target, without retuning."""
from pathlib import Path
import sys,json,hashlib
import joblib,numpy as np,pandas as pd
from model_core import metrics

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/v6_result_to_draw_20261011'
LEGACY=Path(r'C:\Proyectos\SportEdgeAnalyticsV4\deployments\five_models_production_20260828')

def main():
    assert (OUT/'selection_frozen_before_test.json').exists()
    sys.path.insert(0,str(LEGACY))
    p=LEGACY/'artifacts/models/result_to_draw/champion.joblib'
    manifest=json.loads((LEGACY/'artifacts/model_manifest.json').read_text(encoding='utf8'))
    entry=next(q for q in manifest['models'] if q['target']=='result_to_draw')
    digest=hashlib.sha256(p.read_bytes()).hexdigest()
    assert digest==entry['sha256']
    b=joblib.load(p)
    d=pd.read_pickle(OUT/'prepared.pkl')
    frame=d.reindex(columns=b['source_columns'])
    matrix=b['feature_builder'].transform(frame,b['common_engineered']).reindex(columns=b['feature_columns'])
    score=b['estimator'].predict_proba(matrix)[:,1]
    rows=[]
    for partition in ['train','calibration','validation','test']:
        ix=d.index[d.supervised&~d.purged&d.partition.eq(partition)]
        rows.append({'name':'V4_active_hist_gradient_boosting_S06','partition':partition,'candidate_id':b.get('candidate_id','hist_gradient_boosting_S06'),'semantics':'Incumbent trained on historical transition objective; reevaluated here against supplied equalization-and-draw-at-closure target','score_kind':'incumbent API emitted estimator score; no recalibration or threshold retuning','artifact_sha256':digest,**metrics(d.loc[ix,'label'],score[ix],b['threshold'])})
    pd.DataFrame(rows).to_csv(OUT/'incumbent_comparison.csv',index=False)
    pd.DataFrame({'source_row':d.source_row,'id':d.id,'partition':d.partition,'raw_model_score':score,'threshold':b['threshold'],'signal':(score>=b['threshold'])&d.eligible}).to_csv(OUT/'incumbent_replay.csv',index=False)
    print(pd.DataFrame(rows)[['partition','precision','recall','f1','ap','brier']].to_string(index=False))

if __name__=='__main__':main()
