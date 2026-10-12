"""One explicitly historical technical alert through the deployed worker pipeline."""
import asyncio,json,os,sys,subprocess,uuid,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from worker.database import pool
from worker import repository as repo

OUT=ROOT/'outputs/worker_integration_20261012'

def sql(container,user,db,query):
    r=subprocess.run(['docker','exec','-i',container,'psql','-U',user,'-d',db,'-qAt','-v','ON_ERROR_STOP=1'],
      input=query.encode(),capture_output=True,check=True)
    return r.stdout.decode().strip()

async def main():
    os.environ.update(DRAW_V6_DATABASE_URL='postgresql://sportedge_v6_draw@127.0.0.1:15436/sportedge_v6',
      DRAW_V6_DB_PASSWORD_FILE=str(ROOT/'secrets/draw_v6_db_password.txt'),
      DRAW_TELEGRAM_CHAT_ID_FILE=str(Path(r'C:\Proyectos\SportEdgeAnalytics_V6_Home_to_Increase\deployments\SportEdgeIntegracionAnalytics_V6\secrets\telegram_chat_id.txt')))
    s=json.loads((OUT/'technical_test_request.json').read_text(encoding='utf8'))['snapshot']
    fixture=int(s['id'])
    assert sql('sportedge_v2_db','sportedge','sportedge_v2',f'SELECT EXISTS(SELECT 1 FROM public.fixtures WHERE id={fixture})')=='t','Historical fixture missing; never fabricate a production fixture'
    p=await pool();key='technical-test:draw_worker_v1:'+str(s['match_centre_snapshot_id'])
    try:
        async with repo.transaction(p) as c:uid,duplicate=await repo.submit(c,s,source='technical_test',key=key)
        job=outbox=None
        for _ in range(50):
            async with p.acquire() as c:
                job=await c.fetchrow('SELECT * FROM v6_result_to_draw.invocations WHERE invocation_id=$1',uid)
                outbox=await c.fetchrow('SELECT * FROM v6_result_to_draw.telegram_outbox WHERE invocation_id=$1',uid)
            if job['status']=='failed':raise RuntimeError('worker_failed:'+str(job['last_error']))
            if outbox and outbox['status'] in ('failed','uncertain','suppressed'):raise RuntimeError('telegram_'+outbox['status']+':'+str(outbox['last_error']))
            if job['status']=='completed' and outbox and outbox['status']=='sent' and outbox['backend_mirrored']:break
            await asyncio.sleep(.5)
        else:raise RuntimeError('Pipeline did not confirm completion; inspect existing job '+str(uid))
        assert job['actionable_signal'] is False and job['result']['research_only'] is True
        assert outbox['chat_id']=='-5538403637'
        iid,sid,oid=(int(job['backend_inference_id']),int(job['backend_signal_id']),int(outbox['backend_outbox_id']))
        backend=sql('sportedge_v2_db','sportedge','sportedge_v2',f"""SELECT json_build_object(
          'inference_id',i.id,'signal_id',s.id,'outbox_id',o.id,'model_key',i.model_key,'target',s.target,
          'probability',s.probability,'actionable_signal',s.actionable_signal,'outbox_status',o.status,
          'telegram_message_id',o.telegram_message_id,'source',o.payload->>'source')
          FROM public.adjusted_analytic_inferences i JOIN public.adjusted_analytic_signals s ON s.inference_id=i.id
          JOIN public.telegram_alert_outbox o ON o.inference_id=i.id
          WHERE i.id={iid} AND s.id={sid} AND o.id={oid}""")
        b=json.loads(backend)
        assert b['target']=='result_to_draw' and b['outbox_status']=='sent' and b['actionable_signal'] is False
        assert b['telegram_message_id']==outbox['telegram_message_id']
        evidence={'passed':True,'historical_technical_test':True,'not_live_signal':True,'duplicate_request_reused':duplicate,
          'invocation_id':str(uid),'fixture_id':fixture,'snapshot_id':s['match_centre_snapshot_id'],
          'captured_at_original':s['captured_at'],'probability':job['probability'],'threshold':job['threshold'],
          'telegram_chat_id':outbox['chat_id'],'telegram_group':'SportEdge_Latam_Reloaded',
          'telegram_message_id':outbox['telegram_message_id'],'backend':b,'engineering_columns':len(job['engineered_values'])}
        (OUT/'production_pipeline_validation.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf8')
        print('PASS actual worker -> backend tables -> Telegram sent -> shared delivery audit; message',outbox['telegram_message_id'])
    finally:await p.close()

if __name__=='__main__':asyncio.run(main())
