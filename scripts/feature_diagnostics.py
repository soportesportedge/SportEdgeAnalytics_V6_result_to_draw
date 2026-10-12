from pathlib import Path
import json
import numpy as np,pandas as pd,joblib
from sklearn.metrics import average_precision_score
from model_core import FAMILIES,predict,SEED

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/v6_result_to_draw_20261011'

def main():
    records=[]
    for p in (OUT/'feature_audits').glob('*cycle[123].csv'):
        q=pd.read_csv(p);q=q[q.decision.eq('ranked')].sort_values('rank_score',ascending=False).head(70)
        fold=p.stem.split('_cycle')[0];cycle=int(p.stem[-1])
        for r in q.itertuples():records.append({'fold':fold,'cycle':cycle,'feature':r.feature,'rank_score':r.rank_score,'spearman_target':r.spearman_target,'proxy_importance':r.proxy_importance})
    r=pd.DataFrame(records)
    stability=r.groupby(['cycle','feature']).agg(folds_selected=('fold','nunique'),mean_rank_score=('rank_score','mean'),mean_spearman=('spearman_target','mean'),min_spearman=('spearman_target','min'),max_spearman=('spearman_target','max')).reset_index()
    stability.to_csv(OUT/'feature_stability.csv',index=False)
    registry=json.loads((ROOT/'assets/manifest.json').read_text())
    d=pd.read_pickle(OUT/'prepared.pkl');x=pd.read_pickle(OUT/'features.pkl')
    validation=d.index[d.supervised&~d.purged&d.partition.eq('validation')]
    b=joblib.load(ROOT/'assets'/registry['models'][registry['selected_family']]['file'])
    components=[q for w,q in zip(b['weights'],b['components']) if w>0] if b.get('ensemble') else [b]
    selected=set(c for q in components for c in q['transform'].selected)
    # Ten permutations of the fifteen highest proxy-ranked selected features.
    rankings=pd.read_csv(OUT/'feature_audits/main_cycle3.csv')
    top=rankings[rankings.feature.isin(selected)].sort_values('rank_score',ascending=False).head(15).feature.tolist()
    baseline=average_precision_score(d.loc[validation,'label'].to_numpy(int),predict(b,x.loc[validation],d.loc[validation,'snapshot_minute'])[1])
    rng=np.random.default_rng(SEED);result=[]
    z=x.loc[validation].copy()
    for feature in top:
        scores=[]
        original=z[feature].copy()
        for _ in range(10):
            z[feature]=original.to_numpy()[rng.permutation(len(z))]
            p=predict(b,z,d.loc[validation,'snapshot_minute'])[1]
            scores.append(baseline-average_precision_score(d.loc[validation,'label'].to_numpy(int),p))
        z[feature]=original
        result.append({'feature':feature,'model':registry['selected_family'],'repeats':10,'scope':'frozen-model validation diagnostic only; correlated derived columns remain unchanged','ap_drop_mean':float(np.mean(scores)),'ap_drop_sd':float(np.std(scores))})
    pd.DataFrame(result).to_csv(OUT/'champion_permutation_importance.csv',index=False)
    print('Feature stability and ten-repeat diagnostic complete')

if __name__=='__main__':main()
