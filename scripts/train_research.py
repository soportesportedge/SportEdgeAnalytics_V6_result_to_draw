import os
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
from pathlib import Path
from datetime import datetime,timezone
import json,time,hashlib,argparse,copy
import numpy as np
import pandas as pd
import joblib
from model_core import FAMILIES,METHODS,SEED,feature_ranking,FeatureTransform,estimator,configs,Calibrator,select_threshold,metrics,weights,rank,predict,select_voting

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/v6_result_to_draw_20261011'

def jsonwrite(path,obj):path.write_text(json.dumps(obj,indent=2,default=str),encoding='utf8')

def cache_split(d,x,stages,s,name):
    result={};(OUT/'feature_audits').mkdir(exist_ok=True)
    for cycle in range(4):
        z=x.loc[s['train'],stages[cycle]]
        ranking=feature_ranking(z,d.loc[s['train'],'label'].to_numpy(int),d.loc[s['train']])
        pd.DataFrame(ranking['decisions']).to_csv(OUT/'feature_audits'/f'{name}_cycle{cycle}.csv',index=False)
        ranking['corr'].to_csv(OUT/'feature_audits'/f'{name}_cycle{cycle}_spearman.csv')
        for limit in [35,70,120]:
            tr=FeatureTransform(limit).fit(z,ranking)
            result[(cycle,limit)]={'transform':tr,'matrices':{k:tr.transform(x.loc[ix,stages[cycle]]) for k,ix in s.items() if isinstance(ix,pd.Index)}}
    return result

def search(family,candidates,d,stages,s,cache,name,keep=3):
    logfile=OUT/f'iterations_{name}_{family}.jsonl'
    best=[];count=0
    for cfg in candidates:
        start=time.perf_counter();q=cache[(cfg['cycle'],cfg['limit'])];mat=q['matrices']
        est=estimator(family,cfg['params'])
        y=d.loc[s['train'],'label'].to_numpy(int)
        est.fit(mat['train'],y,sample_weight=weights(d.loc[s['train']],y,cfg['balance']))
        rc=est.predict_proba(mat['calibration'])[:,1];rv=est.predict_proba(mat['validation'])[:,1]
        raw_metrics=metrics(d.loc[s['validation'],'label'],rv,1.000001)
        for method in METHODS:
            cal=Calibrator(method).fit(rc,d.loc[s['calibration'],'label'].to_numpy(int),d.loc[s['calibration'],'snapshot_minute'])
            pv=cal.predict(rv,d.loc[s['validation'],'snapshot_minute'])
            threshold,policy,feasible=select_threshold(d.loc[s['validation'],'label'],pv,d.loc[s['validation'],'id'])
            m=metrics(d.loc[s['validation'],'label'],pv,threshold)
            m['signal_fixtures']=int(d.loc[s['validation']].loc[pv>=threshold,'id'].nunique())
            config={**cfg,'calibration':method,'candidate_id':cfg['base_id']+'_'+method}
            record={'experiment':name,'config':config,'validation':m,'raw_validation':raw_metrics,'calibration_effective':cal.method,'decision_policy':policy,'estimator_seconds':time.perf_counter()-start}
            with logfile.open('a',encoding='utf8') as f:f.write(json.dumps(record,default=str)+'\n')
            b={'config':config,'estimator':est,'transform':q['transform'],'calibrator':cal,'stage_columns':stages[cfg['cycle']],'threshold':threshold,'policy':policy,'validation':m,'model_version':'v6_result_to_draw_20261011','target':'result_to_draw'}
            best.append(b);best.sort(key=lambda b:rank(b['validation']),reverse=True);best=best[:keep];count+=1
        if count%25==0:
            top=best[0]
            print(f"{name} {family}: {count} versions; best P={top['validation']['precision']:.3f} R={top['validation']['recall']:.3f} AP={top['validation']['ap']:.3f} policy={top['policy']}",flush=True)
        jsonwrite(OUT/'progress.json',{'experiment':name,'family':family,'versions':count,'best':best[0]['config'],'validation':best[0]['validation'],'updated_at':datetime.now(timezone.utc).isoformat()})
    return best

def forward_split(d,history_end,evaluation_end=None):
    hist=d[d.kickoff.lt(history_end)]
    dates=hist.groupby('id').kickoff.first().drop_duplicates().sort_values()
    ca=dates.iloc[int(len(dates)*.64)];va=dates.iloc[int(len(dates)*.82)]
    ok=d.supervised
    s={
      'train':d.index[ok&d.kickoff.lt(ca)&d.label_available_at.lt(ca)],
      'calibration':d.index[ok&d.kickoff.ge(ca)&d.kickoff.lt(va)&d.label_available_at.lt(va)],
      'validation':d.index[ok&d.kickoff.ge(va)&d.kickoff.lt(history_end)&d.label_available_at.lt(history_end)],
      'evaluation':d.index[ok&d.kickoff.ge(history_end)&(d.kickoff.lt(evaluation_end) if evaluation_end is not None else True)],
      'history_end':history_end,'calibration_start':ca,'validation_start':va}
    assert all(len(s[k])>40 for k in ['train','calibration','validation','evaluation'])
    for a,b in [('train','calibration'),('calibration','validation'),('validation','evaluation')]:assert not(set(d.loc[s[a],'id'])&set(d.loc[s[b],'id']))
    return s

def run_main():
    d=pd.read_pickle(OUT/'prepared.pkl');x=pd.read_pickle(OUT/'features.pkl')
    stages={int(k):v for k,v in json.loads((OUT/'stages.json').read_text()).items()}
    s={k:d.index[d.supervised&~d.purged&d.partition.eq(k)] for k in ['train','calibration','validation']}
    cache=cache_split(d,x,stages,s,'main')
    (OUT/'shortlists').mkdir(exist_ok=True)
    for family in FAMILIES:
        p=OUT/'shortlists'/f'{family}.joblib'
        if p.exists():print('resume completed',family,flush=True);continue
        best=search(family,configs(family),d,stages,s,cache,'main')
        joblib.dump(best,p,compress=3)
    print('MAIN SEARCH COMPLETE; independent test remains closed',flush=True)

def run_folds():
    d=pd.read_pickle(OUT/'prepared.pkl');x=pd.read_pickle(OUT/'features.pkl')
    stages={int(k):v for k,v in json.loads((OUT/'stages.json').read_text()).items()}
    dates=d.groupby('id').kickoff.first().drop_duplicates().sort_values()
    bounds=[dates.iloc[int(len(dates)*q)] for q in [.30,.40,.50,.60,.70,.80]]
    (OUT/'folds').mkdir(exist_ok=True)
    for j in range(5):
        p=OUT/'folds'/f'fold{j+1}.joblib'
        if p.exists():continue
        s=forward_split(d,bounds[j],bounds[j+1]);cache=cache_split(d,x,stages,s,f'fold{j+1}')
        results={};preds=[];foldlog=[];bundles={}
        for family in FAMILIES:
            # Fixed eight configurations chosen without consulting any future fold.
            best=search(family,configs(family,8),d,stages,s,cache,f'fold{j+1}',keep=1)[0]
            bundles[family]=best
            raw,pp=predict(best,x.loc[s['evaluation']],d.loc[s['evaluation'],'snapshot_minute'])
            m=metrics(d.loc[s['evaluation'],'label'],pp,best['threshold'])
            results[family]={'config':best['config'],'inner_validation':best['validation'],'evaluation':m,'threshold':best['threshold']}
            preds.append(pd.DataFrame({'index':s['evaluation'],'source_row':d.loc[s['evaluation'],'source_row'],'id':d.loc[s['evaluation'],'id'],'y':d.loc[s['evaluation'],'label'],'family':family,'fold':j+1,'probability':pp,'raw_model_score':raw,'threshold':best['threshold'],'prediction':pp>=best['threshold']}))
            foldlog.append({'fold':j+1,'family':family,'from':str(bounds[j]),'to':str(bounds[j+1]),**m})
        best,votes=select_voting(bundles,d,x,s['validation'])
        raw,pp=predict(best,x.loc[s['evaluation']],d.loc[s['evaluation'],'snapshot_minute'])
        m=metrics(d.loc[s['evaluation'],'label'],pp,best['threshold'])
        results['soft_voting']={'config':best['config'],'inner_validation':best['validation'],'evaluation':m,'threshold':best['threshold']}
        preds.append(pd.DataFrame({'index':s['evaluation'],'source_row':d.loc[s['evaluation'],'source_row'],'id':d.loc[s['evaluation'],'id'],'y':d.loc[s['evaluation'],'label'],'family':'soft_voting','fold':j+1,'probability':pp,'raw_model_score':raw,'threshold':best['threshold'],'prediction':pp>=best['threshold']}))
        foldlog.append({'fold':j+1,'family':'soft_voting','from':str(bounds[j]),'to':str(bounds[j+1]),**m})
        jsonwrite(OUT/'folds'/f'fold{j+1}_voting.json',votes)
        joblib.dump({'results':results,'predictions':pd.concat(preds,ignore_index=True),'metrics':foldlog,'split':s},p,compress=3)
        print('FOLD',j+1,'COMPLETE',flush=True)
    allfolds=[joblib.load(OUT/'folds'/f'fold{i+1}.joblib') for i in range(5)]
    pd.concat([f['predictions'] for f in allfolds]).to_csv(OUT/'nested_backtesting_predictions.csv',index=False)
    pd.DataFrame([m for f in allfolds for m in f['metrics']]).to_csv(OUT/'nested_backtesting_metrics.csv',index=False)
    jsonwrite(OUT/'nested_protocol.json',{'bounds':[str(q) for q in bounds],'versions_per_family_per_fold':40,'selection':'Each fold independently selects eight estimator configs x five calibrations in earlier train/calibration/validation; evaluation never used for threshold or feature selection.'})

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--phase',choices=['main','folds'],default='main');args=a.parse_args()
    run_main() if args.phase=='main' else run_folds()
