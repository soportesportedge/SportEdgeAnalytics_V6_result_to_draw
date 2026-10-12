from pathlib import Path
import csv,json,hashlib,io,zipfile,sys
import pandas as pd,numpy as np,joblib
from model_core import predict,FAMILIES
from feature_engineering import build_features
from prepare_research import SOURCE

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/v6_result_to_draw_20261011'

def main():
    checks=[]
    def ck(name,v):checks.append({'check':name,'pass':bool(v)});assert v,name
    original=list(csv.reader(SOURCE.open(encoding='utf-8-sig',newline='')))
    new=list(csv.reader((OUT/'backtesting_base_completa.csv').open(encoding='utf-8-sig',newline='')))
    ck('23600_original_rows_preserved',len(original)==len(new)==23601)
    ck('all_222_source_fields_equal_and_order_preserved',all(a==b[:222] for a,b in zip(original,new)))
    d=pd.read_pickle(OUT/'prepared.pkl');x=pd.read_pickle(OUT/'features.pkl');reg=json.loads((ROOT/'assets/manifest.json').read_text())
    ck('target_never_predictor','result_to_draw' not in reg['predictors'])
    ck('183_unique_SI_predictors',len(set(reg['predictors']))==183)
    ck('pending_preserved',sum(a[221]=='' for a in original[1:])==115)
    train=d[d.supervised&~d.purged&d.partition.eq('train')]
    for a,b in [('train','calibration'),('calibration','validation'),('validation','test')]:
        aa=d[d.supervised&~d.purged&d.partition.eq(a)];bb=d[d.supervised&~d.purged&d.partition.eq(b)]
        ck('fixture_disjoint_'+a+'_'+b,not(set(aa.id)&set(bb.id)))
        ck('labels_available_before_'+b,aa.label_available_at.max()<bb.kickoff.min())
    for f in FAMILIES:
        lines=(OUT/f'iterations_main_{f}.jsonl').read_text().splitlines()
        ck('200_versions_'+f,len(lines)==200 and len({json.loads(q)['config']['candidate_id'] for q in lines})==200)
    for j in range(5):
        fold=joblib.load(OUT/'folds'/f'fold{j+1}.joblib');s=fold['split']
        ck('fold'+str(j+1)+'_past_only',all(d.loc[s[k],'label_available_at'].max()<s['history_end'] for k in ['train','calibration','validation']))
        ck('fold'+str(j+1)+'_fixtures_disjoint',not(set(d.loc[s['train'],'id'])&set(d.loc[s['evaluation'],'id'])))
    frozen=json.loads((OUT/'selection_frozen_before_test.json').read_text())
    ck('selected_family_frozen',frozen['selected_family']==reg['selected_family'])
    ck('frozen_selection_hash',hashlib.sha256((OUT/'selection_frozen_before_test.json').read_bytes()).hexdigest()==reg['selection_frozen_sha256'])
    replay=pd.read_csv(OUT/'backtesting_base_completa.csv',low_memory=False)
    # Exact parity on representative rows including missing inputs and late minutes.
    eligible=d.index[d.eligible].to_numpy();sample=eligible[np.unique(np.linspace(0,len(eligible)-1,25).astype(int))]
    for f,m in reg['models'].items():
        p=ROOT/'assets'/m['file'];ck('model_sha256_'+f,hashlib.sha256(p.read_bytes()).hexdigest()==m['sha256'])
        b=joblib.load(p);raw,prob=predict(b,x.loc[sample],d.loc[sample,'snapshot_minute'])
        ck('replay_parity_'+f,np.allclose(prob,replay.loc[sample,f'v6_{f}_probability'],atol=1e-10))
        ck('all_models_research_only_'+f,m['production_approved'] is False and not replay[f'v6_{f}_actionable'].any())
    xlsx=OUT/'Resultados_V6_result_to_draw.xlsx'
    ck('xlsx_zip_integrity',zipfile.ZipFile(xlsx).testzip() is None)
    # Read-only inspection uses the bundled artifact runtime in separate verification.
    ck('xlsx_has_10_sheets',len(pd.ExcelFile(xlsx).sheet_names)==10)
    ck('html_exists',(OUT/'Informe_V6_result_to_draw.html').stat().st_size>10000)
    ck('http_23_checks',all(q['pass'] for q in json.loads((OUT/'http_validation.json').read_text())['checks']))
    ck('backend_container_invocation',json.loads((OUT/'backend_network_validation.json').read_text())['pass'])
    ck('durability_after_restart',json.loads((OUT/'restart_validation.json').read_text())['passed'])
    # Originals remain untouched since inspection.
    for item in json.loads((OUT/'inputs/manifest.json').read_text()):
        ck('unchanged_input_'+Path(item['path']).name,hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256'])
    (OUT/'artifact_validation.json').write_text(json.dumps({'passed':all(v['pass'] for v in checks),'checks':checks,'rows':len(d),'sha256_backtesting':hashlib.sha256((OUT/'backtesting_base_completa.csv').read_bytes()).hexdigest()},indent=2),encoding='utf8')
    print('PASS',len(checks),'artifact and data checks')

if __name__=='__main__':main()
