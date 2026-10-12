from pathlib import Path
import json,zipfile,shutil,hashlib
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/v6_result_to_draw_20261011'
report=ROOT/'reports';report.mkdir(exist_ok=True)
for n in ['Informe_V6_result_to_draw.html','Resultados_V6_result_to_draw.xlsx']:
    shutil.copy2(OUT/n,report/n)
for n in ['artifact_validation.json','http_validation.json','restart_validation.json','backend_network_validation.json','workbook_validation.json','runtime_training_lock.json','champion_permutation_importance.csv','fixture_level_metrics.csv','baselines.csv','champion_test_calibration_bins.csv']:
    shutil.copy2(OUT/n,ROOT/'docs'/n)
shutil.copy2(OUT/'inputs/manifest.json',ROOT/'docs/input_manifest.json')
files=[p for p in OUT.rglob('*') if p.is_file() and p.suffix in ['.csv','.json','.html','.xlsx','.jsonl'] and 'workbook_previews' not in p.parts and p.name not in ['workbook_data.json','progress.json'] and not p.name.endswith('.inspect.ndjson')]
archive=OUT/'Informe_y_Auditoria_result_to_draw_V6.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in files:z.write(p,p.relative_to(OUT))
    for p in (ROOT/'assets').glob('*'):z.write(p,'models/'+p.name)
    z.write(ROOT/'EXPERIMENT_PLAN.md','EXPERIMENT_PLAN.md')
    for n in ['labeling_validation.json','source_inspection.json']:
        z.write(ROOT/'audit'/n,'labeling_audit/'+n)
assert zipfile.ZipFile(archive).testzip() is None
manifest={'zip':archive.name,'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'contains':'complete source-preserving replay, full search logs, feature audits, eight frozen models, metrics and reports','original_inputs_included':False}
(OUT/'delivery_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
print('Delivery archive verified',archive.stat().st_size,'bytes')
