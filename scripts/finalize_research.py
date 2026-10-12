"""Freeze development selection before opening the untouched temporal test."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,csv,io
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from model_core import FAMILIES,SEED,metrics,weights,rank,predict,select_voting,select_threshold
from prepare_research import SOURCE

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/v6_result_to_draw_20261011'
ASSETS=ROOT/'assets'
DEFINITION='Future surviving equalization from abs(dif_score)=1 and draw at observed closure within same (id,market_id); closure is FT or last snapshot only if group reaches minute>=90. Current data contain no official FT captures. Pending cases remain null.'

def write(path,obj):path.write_text(json.dumps(obj,indent=2,default=str,ensure_ascii=False,allow_nan=False),encoding='utf8')

def bootstrap(d,y,pred,n=1000):
    z=pd.DataFrame({'id':d.id.to_numpy(),'tp':np.asarray(y,int)&np.asarray(pred,bool),'fp':(1-np.asarray(y,int))&np.asarray(pred,bool),'fn':np.asarray(y,int)&~np.asarray(pred,bool)})
    sums=z.groupby('id')[['tp','fp','fn']].sum().to_numpy(float)
    rng=np.random.default_rng(SEED);res=[]
    for _ in range(n):
        tp,fp,fn=sums[rng.integers(0,len(sums),size=len(sums))].sum(axis=0)
        res.append([tp/(tp+fp) if tp+fp else np.nan,tp/(tp+fn) if tp+fn else np.nan])
    a=np.asarray(res)
    return {'method':'percentile bootstrap resampling complete fixtures, 1000 replicates','precision_ci95':np.nanquantile(a[:,0],[.025,.975]).tolist() if np.isfinite(a[:,0]).any() else [None,None],'recall_ci95':np.nanquantile(a[:,1],[.025,.975]).tolist() if np.isfinite(a[:,1]).any() else [None,None]}

def snapshot_to_json(d,row):
    # Use original raw columns only; remove future target.
    return {c:None if pd.isna(d.loc[row,c]) else d.loc[row,c].item() if isinstance(d.loc[row,c],np.generic) else d.loc[row,c] for c in d.columns[:222] if c!='result_to_draw'}

def main():
    d=pd.read_pickle(OUT/'prepared.pkl');x=pd.read_pickle(OUT/'features.pkl')
    predictors=json.loads((OUT/'predictors_si.json').read_text())
    finalists={f:joblib.load(OUT/'shortlists'/f'{f}.joblib')[0] for f in FAMILIES}
    val=d.index[d.supervised&~d.purged&d.partition.eq('validation')]
    vote,records=select_voting(finalists,d,x,val);finalists['soft_voting']=vote
    write(OUT/'voting_iterations.json',records)
    nested=pd.read_csv(OUT/'nested_backtesting_predictions.csv')
    aggregate={}
    for f,g in nested.groupby('family'):
        aggregate[f]=metrics(g.y,g.probability,pred=g.prediction)
    write(OUT/'nested_aggregate.json',aggregate)
    # Family selection incorporates stability from development backtesting; never test.
    def final_rank(f):
        m=finalists[f]['validation'];bt=aggregate[f]
        both=m['gates_pass'] and bt['gates_pass']
        return (int(both),int(m['gates_pass']),int(bt['precision']>.5),*rank(m),bt['ap'])
    selected=max(finalists,key=final_rank)
    frozen={'selected_family':selected,'selection_basis':'main validation plus nested development backtesting; independent test unopened','selected_config':finalists[selected]['config'],'main_validation':finalists[selected]['validation'],'nested_backtesting':aggregate[selected],'frozen_at':datetime.now(timezone.utc).isoformat(),'gates':{'precision_strictly_gt':.5,'recall_strictly_gt':.25},'precision_priority':'Never lower precision to optimize F1. Recall relaxation explicitly flagged.','candidate_versions':{f:200 for f in FAMILIES},'ensemble_versions':len(records)}
    frozen_path=OUT/'selection_frozen_before_test.json'
    if frozen_path.exists():
        previous=json.loads(frozen_path.read_text(encoding='utf8'))
        assert previous['selected_family']==selected and previous['selected_config']==frozen['selected_config'], 'Frozen selection cannot be changed after test opening'
        frozen=previous
    else:write(frozen_path,frozen)
    print('SELECTION FROZEN',selected,frozen['main_validation'],flush=True)
    ASSETS.mkdir(exist_ok=True);(ROOT/'docs').mkdir(exist_ok=True)
    test=d.index[d.supervised&~d.purged&d.partition.eq('test')]
    all_metrics=[];calibration=[];ci={};replay={};subgroups=[];fixture_rows=[]
    registry={'target':'result_to_draw','target_definition':DEFINITION,'model_version':'v6_result_to_draw_20261011','selected_family':selected,'predictors':predictors,'models':{},'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'selection_frozen_sha256':hashlib.sha256((OUT/'selection_frozen_before_test.json').read_bytes()).hexdigest()}
    for f,b in finalists.items():
        b['predictors']=predictors
        raw_all,p_all=predict(b,x,d.snapshot_minute)
        decisions=(p_all>=b['threshold'])&d.eligible.to_numpy()
        replay[f]=(raw_all,p_all,decisions)
        for partition in ['train','calibration','validation','test']:
            ix=d.index[d.supervised&~d.purged&d.partition.eq(partition)]
            m=metrics(d.loc[ix,'label'],p_all[ix],b['threshold'])
            m.update({'family':f,'partition':partition,'scope':'resubstitution' if partition=='train' else 'calibrator_fit' if partition=='calibration' else 'selected_on_this_block' if partition=='validation' else 'independent_test','candidate_id':b['config']['candidate_id'],'signal_fixtures':int(d.loc[ix].loc[p_all[ix]>=b['threshold'],'id'].nunique())})
            all_metrics.append(m)
            for variant,probs in [('raw',raw_all[ix]),('calibrated',p_all[ix])]:
                cm=metrics(d.loc[ix,'label'],probs,pred=p_all[ix]>=b['threshold'])
                calibration.append({'family':f,'partition':partition,'probability_kind':variant,**{k:cm[k] for k in ['ap','roc_auc','brier','log_loss','ece_10','probability_mean']}})
            all_metrics.append({'family':f,'partition':partition+'_fixture_weighted','scope':m['scope'],**metrics(d.loc[ix,'label'],p_all[ix],b['threshold'],sample_weight=weights(d.loc[ix]))})
        ci[f]=bootstrap(d.loc[test],d.loc[test,'label'],decisions[test])
        tm=metrics(d.loc[test,'label'],p_all[test],b['threshold'])
        approved=bool(tm['gates_pass'] and b['validation']['gates_pass'] and aggregate[f]['gates_pass'])
        joblib.dump(b,ASSETS/f'{f}.joblib',compress=3)
        registry['models'][f]={'file':f'{f}.joblib','sha256':hashlib.sha256((ASSETS/f'{f}.joblib').read_bytes()).hexdigest(),'candidate_id':b['config']['candidate_id'],'threshold':b['threshold'],'decision_policy':b['policy'],'production_approved':approved,'meets_requested_test_gates':tm['gates_pass'],'test':tm,'nested_development_backtesting':aggregate[f],'test_bootstrap':ci[f],'config':b['config']}
        for variable in ['cluster','league_id']:
            for value,g in d.loc[test].groupby(variable):
                ix=g.index;subgroups.append({'family':f,'group':variable,'value':str(value),**metrics(g.label,p_all[ix],b['threshold'])})
        seen=set(d.loc[d.partition.eq('train')&d.supervised,'league_id'])
        for status in [True,False]:
            ix=d.loc[test].index[d.loc[test,'league_id'].isin(seen).eq(status)]
            if len(ix):subgroups.append({'family':f,'group':'league_seen_in_training','value':str(status),**metrics(d.loc[ix,'label'],p_all[ix],b['threshold'])})
        for fixture,g in d.loc[test].sort_values('capture').groupby('id'):
            signaled=g.index[decisions[g.index]]
            fixture_rows.append({'family':f,'id':int(fixture),'has_signal':bool(len(signaled)),'positive_fixture':bool(g.label.max()),'first_signal_y':int(d.loc[signaled[0],'label']) if len(signaled) else None,'signals':len(signaled)})
    write(ASSETS/'manifest.json',registry)
    write(OUT/'test_bootstrap.json',ci)
    pd.DataFrame(all_metrics).to_csv(OUT/'metrics_comparison.csv',index=False)
    pd.DataFrame(calibration).to_csv(OUT/'calibration_before_after.csv',index=False)
    pd.DataFrame(subgroups).to_csv(OUT/'test_subgroups.csv',index=False)
    pd.DataFrame(fixture_rows).to_csv(OUT/'test_fixture_first_signal.csv',index=False)
    # Simple causal baselines trained only on train and threshold selected on validation.
    train=d.index[d.supervised&~d.purged&d.partition.eq('train')]
    baselines=[]
    prevalence=float(d.loc[train,'label'].mean())
    for partition,ix in [('validation',val),('test',test)]:
        for name,pred in [('always_positive',np.ones(len(ix),bool)),('always_negative',np.zeros(len(ix),bool))]:
            baselines.append({'name':name,'partition':partition,**metrics(d.loc[ix,'label'],np.full(len(ix),prevalence),pred=pred)})
    cols=['snapshot_minute','eng_trailing_shots_on_target','eng_trailing_shots_on_target_10m','eng_trailing_advantage_dangerous_attacks_10m','eng_trailing_strength']
    med=x.loc[train,cols].median().fillna(0)
    base=HistGradientBoostingClassifier(max_iter=80,max_leaf_nodes=5,min_samples_leaf=40,l2_regularization=10,early_stopping=False,random_state=SEED).fit(x.loc[train,cols].fillna(med),d.loc[train,'label'].to_numpy(int),sample_weight=weights(d.loc[train]))
    pv=base.predict_proba(x.loc[val,cols].fillna(med))[:,1]
    t,pol,_=select_threshold(d.loc[val,'label'],pv,d.loc[val,'id'])
    for partition,ix in [('validation',val),('test',test)]:
        pp=base.predict_proba(x.loc[ix,cols].fillna(med))[:,1]
        baselines.append({'name':'five_feature_shallow_hgb','partition':partition,'policy':pol,**metrics(d.loc[ix,'label'],pp,t)})
    pd.DataFrame(baselines).to_csv(OUT/'baselines.csv',index=False)
    # Replay every input record, preserving original byte-level record bodies.
    header=['v6_source_row','v6_partition','v6_label_status','v6_training_cohort','v6_eligible','v6_closed','v6_terminal','v6_nested_fold']
    nested_cols={}
    for f in finalists:
        header += [f'v6_{f}_{c}' for c in ['raw_score','probability','threshold','signal','actionable','nested_probability','nested_threshold','nested_signal']]
        nf=nested[nested.family.eq(f)].set_index('source_row')
        nested_cols[f]=nf
    lines=SOURCE.read_bytes().splitlines(keepends=True)
    assert len(lines)==len(d)+1,'Source contains multiline records; use a CSV-aware preservation path'
    def appended(line,fields):
        ending=b'\r\n' if line.endswith(b'\r\n') else b'\n' if line.endswith(b'\n') else b''
        body=line[:-len(ending)] if ending else line
        ss=io.StringIO();csv.writer(ss,lineterminator='').writerow(fields)
        return body+b','+ss.getvalue().encode('utf8')+ending
    with (OUT/'backtesting_base_completa.csv').open('wb') as dest:
        dest.write(appended(lines[0],header))
        for i,r in enumerate(d.itertuples()):
            sr=r.source_row
            fold=next((int(v.loc[sr,'fold']) for v in nested_cols.values() if sr in v.index),None)
            row=[sr,r.partition,'pending' if pd.isna(r.label) else 'resolved',r.supervised,r.eligible,r.closed,r.terminal,fold]
            for f in finalists:
                rr,pp,ss=replay[f];nf=nested_cols[f];nr=nf.loc[sr] if sr in nf.index else None
                row += [float(rr[i]) if r.eligible else None,float(pp[i]) if r.eligible else 0.,finalists[f]['threshold'],bool(ss[i]),bool(ss[i] and registry['models'][f]['production_approved']),None if nr is None else float(nr.probability),None if nr is None else float(nr.threshold),None if nr is None else bool(nr.prediction)]
            dest.write(appended(lines[i+1],row))
    original=list(csv.reader(io.StringIO(SOURCE.read_text(encoding='utf-8-sig'))))
    replayed=list(csv.reader((OUT/'backtesting_base_completa.csv').open(encoding='utf-8-sig',newline='')))
    assert len(original)==len(replayed) and all(a==b[:222] for a,b in zip(original,replayed))
    # Main frozen final model applied to entire base is replay, not a past-only forecast.
    replay_metrics=[]
    for f,(rr,pp,ss) in replay.items():
        ix=d.index[d.label.notna()]
        replay_metrics.append({'family':f,'scope':'full_labeled_base_replay_includes_training',**metrics(d.loc[ix,'label'],np.where(d.eligible,pp,0)[ix],finalists[f]['threshold'])})
        ix=d.index[d.supervised&~d.purged]
        replay_metrics.append({'family':f,'scope':'eligible_nonterminal_replay_includes_training',**metrics(d.loc[ix,'label'],pp[ix],finalists[f]['threshold'])})
    pd.DataFrame(replay_metrics).to_csv(OUT/'full_base_replay_metrics.csv',index=False)
    ix=d.index[d.eligible].to_numpy()[0]
    write(ROOT/'docs/example_request.json',{'request_id':'bbe3e979-478e-4d5b-a789-4b0618b2df9d','snapshot':snapshot_to_json(d,ix)})
    versionlogs=list(OUT.glob('iterations_*.jsonl'))
    iteration_records=[json.loads(line) for p in versionlogs for line in p.read_text(encoding='utf8').splitlines()]
    pd.DataFrame([{'experiment':r['experiment'],'family':r['config']['family'],'candidate_id':r['config']['candidate_id'],'cycle':r['config']['cycle'],'limit':r['config']['limit'],'balance':r['config']['balance'],'calibration':r['config']['calibration'],'calibration_effective':r['calibration_effective'],'policy':r['decision_policy'],'params':json.dumps(r['config']['params']),**r['validation']} for r in iteration_records]).to_csv(OUT/'all_iterations.csv',index=False)
    final_features=[]
    def effective(b,f):
        if b.get('ensemble'):
            for w,component in zip(b['weights'],b['components']):
                if w>0:effective(component,f+'__'+component['config']['family'])
        else:
            for name in b['transform'].names:final_features.append({'model':f,'candidate_id':b['config']['candidate_id'],'feature':name,'cycle':b['config']['cycle'],'kind':'engineered' if name.startswith('eng_') else 'missing_flag' if name.startswith('missing__') else 'categorical' if name.startswith(('league_id_','cluster_')) else 'source'})
    for f,b in finalists.items():effective(b,f)
    pd.DataFrame(final_features).to_csv(OUT/'effective_model_variables.csv',index=False)
    write(OUT/'completion_summary.json',{'selected_family':selected,'production_approved':registry['models'][selected]['production_approved'],'test':registry['models'][selected]['test'],'nested':aggregate[selected],'main_versions':1400,'nested_versions':1400,'ensemble_versions_main':len(records),'ensemble_versions_folds':5*len(records),'replay_source_rows':len(d),'source_values_preserved':True,'backtest_rows_per_family':int(nested.groupby('family').size().iloc[0]),'test_rows':len(test),'test_fixture_count':int(d.loc[test].id.nunique()),'unresolved_preserved':int(d.label.isna().sum()),'model_count':len(finalists),'completed_at':datetime.now(timezone.utc).isoformat()})
    print(json.dumps(json.loads((OUT/'completion_summary.json').read_text()),indent=2),flush=True)

if __name__=='__main__':main()
