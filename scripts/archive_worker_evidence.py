from pathlib import Path
import shutil,json,subprocess
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/worker_integration_20261012'
DEST=ROOT/'docs/worker_validation';DEST.mkdir(exist_ok=True)
for name in ['isolated_tests.json','isolated_real_model_validation.json','production_schema_installation.json','production_pipeline_validation.json']:
    shutil.copy2(OUT/name,DEST/name)
# No credential files are copied into docs or version control.
summary={'integration':'result_to_draw','deployed':True,'chat':'SportEdge_Latam_Reloaded','chat_id':'-5538403637',
 'containers':['sportedge_v6_result_to_draw','sportedge_v6_result_to_draw_worker','sportedge_v6_result_to_draw_telegram'],
 'model_key':'analytics_v6_result_to_draw_20261011','experimental':True,'actionable_signal':False,
 'isolated_checks':len(json.loads((OUT/'isolated_tests.json').read_text())['checks']),
 'production_technical_message_id':json.loads((OUT/'production_pipeline_validation.json').read_text())['telegram_message_id'],
 'live_signal_observed_during_verification':False}
(DEST/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
print('Worker evidence archived without credentials')
