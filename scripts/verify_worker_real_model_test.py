"""Actual model HTTP + isolated worker databases + simulated Telegram."""
import asyncio,os,json,uuid,sys
from pathlib import Path
import pandas as pd,numpy as np,asyncpg,httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from worker.database import pool,init
from worker import repository as repo,telegram
from worker.inference import process_one

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/worker_integration_20261012'

async def main():
    password=(OUT/'test_password.txt').read_text().strip()
    os.environ.update(DRAW_V6_DATABASE_URL='postgresql://draw_test_v6_worker@127.0.0.1:15438/draw_test_v6',
      DRAW_BACKEND_DATABASE_URL='postgresql://draw_test_backend_writer@127.0.0.1:15438/draw_test_backend',
      DRAW_V6_DB_PASSWORD=password,DRAW_BACKEND_DB_PASSWORD=password,DRAW_MODEL_API_KEY='integration-test-key',
      DRAW_MODEL_URL='http://127.0.0.1:8793',DRAW_TELEGRAM_BOT_TOKEN='test-token',
      DRAW_TELEGRAM_CHAT_ID='-5538403637',DRAW_TELEGRAM_ENABLED='true')
    data=pd.read_csv(ROOT/'outputs/v6_result_to_draw_20261011/backtesting_base_completa.csv',low_memory=False)
    hit=data[(data.v6_soft_voting_probability>=data.v6_soft_voting_threshold)&data.v6_training_cohort].iloc[0]
    s={c:None if pd.isna(hit[c]) else hit[c].item() if isinstance(hit[c],np.generic) else hit[c] for c in data.columns[:221]}
    v6=await pool();backend=await pool('backend')
    admin=await asyncpg.connect('postgresql://postgres@127.0.0.1:15438/draw_test_backend',password=password);await init(admin)
    adminv6=await asyncpg.connect('postgresql://postgres@127.0.0.1:15438/draw_test_v6',password=password);await init(adminv6)
    try:
        await admin.execute('INSERT INTO public.fixtures(id) VALUES($1) ON CONFLICT DO NOTHING',int(s['id']))
        async with repo.transaction(v6) as c:uid,_=await repo.submit(c,s,source='technical_test',key='real-model-test:'+str(uuid.uuid4()))
        async with httpx.AsyncClient() as client:await process_one(v6,backend,client,uid)
        job=await adminv6.fetchrow('SELECT * FROM v6_result_to_draw.invocations WHERE invocation_id=$1',uid)
        assert job['status']=='completed',job['last_error']
        assert job['notification_allowed'] and len(job['engineered_values'])==136
        legacy=await admin.fetchrow('SELECT * FROM public.adjusted_analytic_signals WHERE id=$1',job['backend_signal_id'])
        assert legacy['target']=='result_to_draw' and legacy['probability']==job['probability'] and legacy['actionable_signal'] is False
        captured=[]
        def mocked_send(request):
            captured.append(json.loads(request.content));return httpx.Response(200,json={'ok':True,'result':{'message_id':99999}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(mocked_send)) as client:await telegram.deliver_one(v6,client)
        await telegram.mirror_sent(v6,backend)
        row=await adminv6.fetchrow('SELECT * FROM v6_result_to_draw.telegram_outbox WHERE invocation_id=$1',uid)
        assert row['status']=='sent' and row['backend_mirrored']
        evidence={'passed':True,'model_api_http_real':True,'telegram_calls_simulated':True,'production_database_modified':False,
          'request_id':str(uid),'fixture_id':int(s['id']),'snapshot_id':s.get('match_centre_snapshot_id'),
          'probability':job['probability'],'threshold':job['threshold'],'engineering_columns':136,
          'backend_inference_id':job['backend_inference_id'],'backend_signal_id':job['backend_signal_id'],
          'telegram_preview':captured[0]['text'],'actionable_signal':False}
        (OUT/'isolated_real_model_validation.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf8')
        (OUT/'technical_test_request.json').write_text(json.dumps({'snapshot':s},ensure_ascii=False,indent=2),encoding='utf8')
        print('PASS actual model -> isolated queue -> Analytics V3/Five Models tables -> simulated Telegram -> delivery audit')
    finally:await v6.close();await backend.close();await admin.close();await adminv6.close()

if __name__=='__main__':asyncio.run(main())
