"""All database writes are restricted to the disposable PostgreSQL on port 15438."""
import asyncio,copy,json,os,uuid
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch,AsyncMock
import asyncpg,httpx
from worker import repository as repo
from worker import telegram
from worker.inference import process_one
from worker.database import pool,init
from worker.config import MODEL,MODEL_VERSION,FAMILY,MODEL_KEY

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/worker_integration_20261012'

def test_isolated_pipeline():
    asyncio.run(scenarios())

async def scenarios():
    password=(OUT/'test_password.txt').read_text().strip()
    os.environ.update(DRAW_V6_DATABASE_URL='postgresql://draw_test_v6_worker@127.0.0.1:15438/draw_test_v6',
      DRAW_BACKEND_DATABASE_URL='postgresql://draw_test_backend_writer@127.0.0.1:15438/draw_test_backend',
      DRAW_V6_DB_PASSWORD=password,DRAW_BACKEND_DB_PASSWORD=password,
      DRAW_MODEL_API_KEY='test-model-key',DRAW_TELEGRAM_BOT_TOKEN='test-token',
      DRAW_TELEGRAM_CHAT_ID='-5538403637',DRAW_TELEGRAM_ENABLED='true')
    assert 'draw_test_' in os.environ['DRAW_V6_DATABASE_URL'] and ':15438/' in os.environ['DRAW_BACKEND_DATABASE_URL']
    v6=await pool();backend=await pool('backend')
    admin_v6=await asyncpg.connect('postgresql://postgres@127.0.0.1:15438/draw_test_v6',password=password);await init(admin_v6)
    admin_backend=await asyncpg.connect('postgresql://postgres@127.0.0.1:15438/draw_test_backend',password=password);await init(admin_backend)
    checks=[]
    def ck(name,v):checks.append({'check':name,'pass':bool(v)});assert v,name
    template=json.loads((ROOT/'docs/example_request.json').read_text())['snapshot']
    engineered={q['feature']:None for q in json.loads((ROOT/'docs/feature_catalog.json').read_text())}
    def snapshot(fixture,minute=75,old=False):
        s=copy.deepcopy(template);s.update(id=fixture,snapshot_minute=minute,home_score=0,away_score=1,dif_score=-1,
          captured_at=(datetime.now(timezone.utc)-timedelta(seconds=600 if old else 1)).isoformat(),state_developer_name='INPLAY_2ND_HALF')
        return s
    def result(job,p=.8):
        s=job['input_values'];eligible=s['state_developer_name']=='INPLAY_2ND_HALF' and s['snapshot_minute']>=70 and abs(s['dif_score'])==1
        return {'request_id':str(job['invocation_id']),'fixture_id':int(s['id']),'captured_at':s['captured_at'],
          'target':'result_to_draw','family':FAMILY,'model_version':MODEL_VERSION,'candidate_id':MODEL['candidate_id'],
          'artifact_sha256':MODEL['sha256'],'probability':p if eligible else None,'raw_model_score':p if eligible else None,
          'threshold':MODEL['threshold'],'signal':bool(eligible and p>=MODEL['threshold']),'eligible':eligible,
          'actionable_signal':False,'production_approved':False,'research_only':True,'engineered_features':engineered,
          'created_at':datetime.now(timezone.utc).isoformat(),'calibration_method':'component_calibrations'}
    async def submit(s,source='v6_collector',key=None):
        async with repo.transaction(v6) as c:return await repo.submit(c,s,source=source,key=key or str(uuid.uuid4()))
    async def complete_direct(s,source='v6_collector',p=.8):
        uid,_=await submit(s,source);job=await repo.claim(v6,uid);r=result(job,p)
        b=await repo.mirror(backend,job,r);await repo.complete(v6,job,r,b)
        return uid,job,r,b
    async def clean_pending():
        await admin_v6.execute("UPDATE v6_result_to_draw.telegram_outbox SET status='suppressed',last_error='isolated_test_reset' WHERE status='pending'")
    try:
        # The backend worker has no direct write privilege to shared legacy tables.
        try:
            async with backend.acquire() as c:await c.execute("INSERT INTO public.adjusted_analytic_inferences(fixture_id,snapshot_minute,model_key,input_record,response_json) VALUES(1,75,'bad','{}','{}')")
        except asyncpg.InsufficientPrivilegeError:ck('writer_cannot_directly_modify_shared_tables',True)
        else:ck('writer_cannot_directly_modify_shared_tables',False)
        control_id=await admin_backend.fetchval("INSERT INTO public.adjusted_analytic_inferences(fixture_id,snapshot_minute,model_key,input_record,response_json) VALUES(11,75,'analytics_v4_production_20260828','{}','{\"control\":true}') RETURNING id")
        source_id=uuid.uuid4();s=snapshot(11)
        await admin_v6.execute("INSERT INTO v6.match_centre_invocations(invocation_id,idempotency_key,input_values,source,captured_at) VALUES($1,$2,$3,'match_centre_readonly',$4::text::timestamptz)",source_id,'source-test',s,s['captured_at'])
        ck('feeds_existing_v6_input_journal',await repo.collect(v6)==1)
        ck('feed_deduplication',await repo.collect(v6)==0)
        uid=await admin_v6.fetchval('SELECT invocation_id FROM v6_result_to_draw.invocations WHERE source_invocation_id=$1',source_id)
        a,b=await asyncio.gather(repo.claim(v6,uid),repo.claim(v6,uid))
        ck('concurrent_claim_only_one_worker',sum(q is not None for q in [a,b])==1)
        await admin_v6.execute("UPDATE v6_result_to_draw.invocations SET leased_until=now()-interval '1 second' WHERE invocation_id=$1",uid)
        cache={};calls=[]
        def model_mock(request):
            payload=json.loads(request.content);rid=payload['request_id'];calls.append(rid)
            if rid not in cache:cache[rid]=result({'invocation_id':uuid.UUID(rid),'input_values':payload['snapshot']})
            return httpx.Response(200,json=cache[rid])
        async with httpx.AsyncClient(transport=httpx.MockTransport(model_mock)) as client:
            with patch('worker.repository.complete',new=AsyncMock(side_effect=asyncpg.SerializationError('test lost local commit'))):
                await process_one(v6,backend,client,uid)
            ck('backend_commit_precedes_local_outbox',await admin_backend.fetchval('SELECT count(*) FROM analytics_v6_result_to_draw.requests WHERE request_id=$1',uid)==1)
            await admin_v6.execute('UPDATE v6_result_to_draw.invocations SET next_attempt_at=now() WHERE invocation_id=$1',uid)
            await process_one(v6,backend,client,uid)
        ck('retry_preserves_model_request_uuid',calls==[str(uid),str(uid)])
        ck('retry_does_not_duplicate_backend_result',await admin_backend.fetchval('SELECT count(*) FROM analytics_v6_result_to_draw.requests WHERE request_id=$1',uid)==1)
        row=await admin_v6.fetchrow('SELECT * FROM v6_result_to_draw.invocations WHERE invocation_id=$1',uid)
        ck('job_completes_with_backend_ids',row['status']=='completed' and row['backend_inference_id'] and row['backend_signal_id'])
        ck('one_target_only_in_five_models_table',await admin_backend.fetchval('SELECT count(*) FROM public.adjusted_analytic_signals WHERE inference_id=$1 AND target=$2',row['backend_inference_id'],'result_to_draw')==1)
        ck('experimental_signal_not_marked_actionable',await admin_backend.fetchval('SELECT actionable_signal FROM public.adjusted_analytic_signals WHERE id=$1',row['backend_signal_id']) is False)
        ck('prior_v4_control_unchanged',await admin_backend.fetchval('SELECT response_json FROM public.adjusted_analytic_inferences WHERE id=$1',control_id)=={'control':True})
        ck('source_values_preserved',await admin_backend.fetchval('SELECT input_record FROM public.adjusted_analytic_inferences WHERE id=$1',row['backend_inference_id'])==s)
        bad=snapshot(12);bad['result_to_draw']=True;rejected,_=await submit(bad)
        ck('future_target_rejected',await admin_v6.fetchval('SELECT status FROM v6_result_to_draw.invocations WHERE invocation_id=$1',rejected)=='rejected')
        bad=snapshot(13);bad.pop('home_possession');rejected,_=await submit(bad)
        ck('missing_predictor_rejected',await admin_v6.fetchval('SELECT status FROM v6_result_to_draw.invocations WHERE invocation_id=$1',rejected)=='rejected')
        job=dict(row);r=copy.deepcopy(cache[str(uid)]);r['target']='home_score_to_increase'
        try:await repo.mirror(backend,job,r)
        except asyncpg.RaiseError:ck('backend_rejects_wrong_target',True)
        else:ck('backend_rejects_wrong_target',False)
        changed=copy.deepcopy(s);changed['home_possession']=99
        job['input_values']=changed
        try:await repo.mirror(backend,job,cache[str(uid)])
        except asyncpg.RaiseError:ck('backend_idempotency_conflict_rejected',True)
        else:ck('backend_idempotency_conflict_rejected',False)
        _,_,_,stale=await complete_direct(snapshot(22,old=True))
        ck('stale_inference_saved_without_notification',not stale['notification_allowed'])
        _,_,_,fresh=await complete_direct(snapshot(22,minute=76))
        ck('stale_signal_does_not_reserve_future_alert',fresh['notification_allowed'])
        uid2,j2,r2,first=await complete_direct(snapshot(23,old=True),'technical_test')
        ck('explicit_historical_technical_test_allowed',first['notification_allowed'])
        _,_,_,repeated=await complete_direct(snapshot(23,minute=76,old=True),'technical_test')
        ck('same_fixture_score_deduplicated',repeated['suppressed'] and not repeated['notification_allowed'])
        new_score=snapshot(23,minute=77,old=True);new_score.update(home_score=1,away_score=2)
        _,_,_,newalert=await complete_direct(new_score,'technical_test')
        ck('changed_score_can_create_new_alert',newalert['notification_allowed'])
        newer=snapshot(31);_,_,_,latest=await complete_direct(newer,p=.2)
        earlier=copy.deepcopy(newer);earlier['captured_at']=(datetime.fromisoformat(newer['captured_at'])-timedelta(seconds=10)).isoformat()
        _,_,_,sup=await complete_direct(earlier,p=.8)
        ck('same_minute_older_capture_cannot_replace_newer',sup['superseded'] and sup['inference_id']==latest['inference_id'])
        ck('latest_probability_preserved',await admin_backend.fetchval('SELECT probability FROM public.adjusted_analytic_signals WHERE id=$1',latest['signal_id'])==.2)
        await clean_pending()
        # An accepted but unread Telegram response is uncertain, never automatically resent.
        uid3,_,_,_=await complete_direct(snapshot(41,old=True),'technical_test');tg_calls=[]
        def timeout_mock(request):tg_calls.append(1);raise httpx.ReadTimeout('isolated read timeout',request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_mock)) as client:
            await telegram.deliver_one(v6,client);await telegram.deliver_one(v6,client)
        ck('telegram_unknown_delivery_not_retried',len(tg_calls)==1 and await admin_v6.fetchval('SELECT status FROM v6_result_to_draw.telegram_outbox WHERE invocation_id=$1',uid3)=='uncertain')
        uid4,_,_,_=await complete_direct(snapshot(42,old=True),'technical_test')
        def rate_mock(request):return httpx.Response(429,json={'ok':False,'parameters':{'retry_after':7}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(rate_mock)) as client:await telegram.deliver_one(v6,client)
        status=await admin_v6.fetchrow('SELECT status,last_error,next_attempt_at>now() AS later FROM v6_result_to_draw.telegram_outbox WHERE invocation_id=$1',uid4)
        ck('telegram_rate_limit_schedules_retry',status['status']=='pending' and status['last_error']=='telegram_rate_limit' and status['later'])
        await clean_pending()
        uid5,_,_,_=await complete_direct(snapshot(43,old=True),'technical_test');sent=[]
        def send_mock(request):
            body=json.loads(request.content);sent.append(body)
            return httpx.Response(200,json={'ok':True,'result':{'message_id':98765}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(send_mock)) as client:await telegram.deliver_one(v6,client)
        ck('telegram_uses_confirmed_new_group',sent[0]['chat_id']=='-5538403637')
        ck('telegram_historical_test_clearly_labeled','No es una señal en vivo' in sent[0]['text'] and '30,2%' in sent[0]['text'])
        await telegram.mirror_sent(v6,backend);await telegram.mirror_sent(v6,backend)
        sentrow=await admin_v6.fetchrow('SELECT * FROM v6_result_to_draw.telegram_outbox WHERE invocation_id=$1',uid5)
        ck('telegram_success_mirrored_to_legacy_outbox',sentrow['backend_mirrored'] and sentrow['backend_outbox_id'])
        ck('legacy_outbox_sent_only',await admin_backend.fetchval('SELECT status FROM public.telegram_alert_outbox WHERE id=$1',sentrow['backend_outbox_id'])=='sent')
        ck('delivery_mirror_idempotent',await admin_backend.fetchval('SELECT count(*) FROM public.telegram_alert_outbox WHERE dedup_key=$1',sentrow['notification_key'])==1)
        # Late pending notifications are suppressed; they remain auditable.
        uid6,_,_,_=await complete_direct(snapshot(44,old=True),'technical_test')
        await admin_v6.execute("UPDATE v6_result_to_draw.telegram_outbox SET created_at=now()-interval '1 hour' WHERE invocation_id=$1",uid6)
        ck('stale_outbox_suppressed',await telegram.claim(v6) is None and await admin_v6.fetchval('SELECT status FROM v6_result_to_draw.telegram_outbox WHERE invocation_id=$1',uid6)=='suppressed')
        await repo.heartbeat(v6,'inference',{'running':True})
        ck('worker_heartbeat_recorded',await admin_v6.fetchval("SELECT heartbeat_at>now()-interval '1 minute' FROM v6_result_to_draw.worker_status WHERE component='inference'"))
        (OUT/'isolated_tests.json').write_text(json.dumps({'passed':True,'checks':checks,'production_database_modified':False,'telegram_network_calls':0,'test_database_container':'sportedge_draw_isolated_test_db'},indent=2),encoding='utf8')
        print('PASS',len(checks),'isolated integration checks')
    finally:
        await v6.close();await backend.close();await admin_v6.close();await admin_backend.close()
