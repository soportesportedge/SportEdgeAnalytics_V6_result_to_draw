from pathlib import Path
import json, hashlib
import numpy as np
import pandas as pd
from feature_engineering import build_features

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/v6_result_to_draw_20261011'
SOURCE=ROOT/'outputs/etiquetado_result_to_draw_20261011/Match_centre_base_modelos_V2_etiquetada_result_to_draw.csv'

def main():
    dictionary=json.loads((OUT/'inputs/Descripcion_variables_actualizada_V2_analitico.json').read_text(encoding='utf8'))['Variables']
    predictors=[r[2] for r in dictionary[1:] if str(r[1]).strip().upper()=='SI']
    d=pd.read_csv(SOURCE,low_memory=False)
    assert len(predictors)==183 and len(set(predictors))==183
    assert all(c in d for c in predictors)
    assert not {'result_to_draw','id','captured_at','starting_at','market_id'}&set(predictors)
    (OUT/'predictors_si.json').write_text(json.dumps(predictors,indent=2),encoding='utf8')
    audit=json.loads((ROOT/'audit/labeling_validation.json').read_text(encoding='utf8'))
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==audit['output_sha256']
    closure={ (str(g['id']),str(g['market_id'])):g for g in audit['group_details'] }
    d['source_row']=np.arange(len(d))+2
    d['label']=d.result_to_draw.map({True:1,False:0}).astype('Float64')
    d['kickoff']=pd.to_datetime(d.starting_at,format='mixed',utc=True)
    d['capture']=pd.to_datetime(d.captured_at,format='mixed',utc=True)
    close_time=[];terminal=[];closed=[]
    for r in d.itertuples():
        m='' if pd.isna(r.market_id) else str(int(r.market_id))
        g=closure[(str(r.id),m)]
        cr=g['closure_source_csv_row']
        close_time.append(pd.NaT if cr is None else d.capture.iloc[cr-2])
        terminal.append(cr is not None and r.source_row>=cr)
        closed.append(cr is not None)
    d['label_available_at']=pd.to_datetime(close_time,utc=True)
    d['terminal']=terminal;d['closed']=closed
    d['eligible']=d.dif_score.abs().eq(1)&d.snapshot_minute.ge(70)&d.state_developer_name.eq('INPLAY_2ND_HALF')
    keys=['id','captured_at','snapshot_minute']
    conflicts=d.groupby(keys,dropna=False).label.nunique().gt(1)
    conflict_keys=set(conflicts[conflicts].index.tolist())
    d['conflicting_market_label']=[tuple(v) in conflict_keys for v in d[keys].values]
    d['duplicate_snapshot']=d.duplicated(keys,keep='first')
    d['supervised']=d.eligible&d.closed&~d.terminal&d.label.notna()&~d.duplicate_snapshot&~d.conflicting_market_label
    fx=d.groupby('id').kickoff.first().sort_values()
    dates=fx.drop_duplicates().sort_values()
    cut=[dates.iloc[int(len(dates)*q)] for q in [.50,.65,.80]]
    d['partition']=np.select([d.kickoff<cut[0],d.kickoff<cut[1],d.kickoff<cut[2]],['train','calibration','validation'],default='test')
    d['purged']=False
    for name,boundary in zip(['train','calibration','validation'],cut):
        d.loc[d.partition.eq(name)&d.label_available_at.ge(boundary),'purged']=True
    d.to_pickle(OUT/'prepared.pkl')
    x,stages,catalog=build_features(d,predictors)
    x.to_pickle(OUT/'features.pkl')
    (OUT/'feature_catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf8')
    (OUT/'stages.json').write_text(json.dumps(stages,indent=2),encoding='utf8')
    rows=[]
    for p,g in d.groupby('partition',sort=False):
        z=g[g.supervised&~g.purged]
        rows.append({'partition':p,'source_rows':len(g),'fixtures':g.id.nunique(),'supervised_rows':len(z),'supervised_fixtures':z.id.nunique(),'positives':int(z.label.sum()),'prevalence':float(z.label.mean()),'purged':int((g.supervised&g.purged).sum()),'from':str(g.kickoff.min()),'to':str(g.kickoff.max())})
    pd.DataFrame(rows).to_csv(OUT/'partitions.csv',index=False)
    d[['source_row','id','market_id','captured_at','starting_at','snapshot_minute','label','label_available_at','eligible','closed','terminal','duplicate_snapshot','conflicting_market_label','supervised','partition','purged']].to_csv(OUT/'row_audit.csv',index=False)
    profile=[]
    for c in predictors:
        s=d[c];ns=pd.to_numeric(s,errors='coerce')
        profile.append({'variable':c,'missing':float(s.isna().mean()),'unique':s.nunique(),'zero':float(ns.eq(0).mean()),'min':ns.min(),'median':ns.median(),'max':ns.max(),'type':str(s.dtype),'dictionary_group':next(r[7] for r in dictionary[1:] if r[2]==c)})
    pd.DataFrame(profile).to_csv(OUT/'eda_variables.csv',index=False)
    for c in ['cluster','league_id','snapshot_minute','dif_score']:
        d.groupby(c,dropna=False).agg(rows=('id','size'),fixtures=('id','nunique'),resolved=('label','count'),positives=('label','sum'),prevalence=('label','mean')).to_csv(OUT/f'eda_{c}.csv')
    summary={'rows':len(d),'columns_source':222,'fixtures':d.id.nunique(),'predictors_si':predictors,'engineered_features':len(catalog),'stages':{k:len(v) for k,v in stages.items()},'positive_rows':int(d.label.sum()),'pending':int(d.label.isna().sum()),'eligible_rows':int(d.eligible.sum()),'supervised_rows':int(d.supervised.sum()),'supervised_fixtures':d[d.supervised].id.nunique(),'duplicates':int(d.duplicate_snapshot.sum()),'conflicting_rows':int(d.conflicting_market_label.sum()),'dictionary_target_mismatch':'Requested XLSX defines home_score_to_increase; only PREDICTORA=SI fields used. Target preserved from labeled CSV and result_to_draw XLSM audit.','source_sha256':audit['output_sha256'],'cutoffs':[str(c) for c in cut],'partitions':rows}
    (OUT/'preparation_summary.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf8')
    print(json.dumps({k:v for k,v in summary.items() if k!='predictors_si'},indent=2,default=str))

if __name__=='__main__':main()
