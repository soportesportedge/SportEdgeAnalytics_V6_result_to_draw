import hashlib,json,uuid
from worker.config import FAMILY,secret,MODEL_KEY,max_age,bound
from worker.database import transaction
from worker.policy import validate_snapshot

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

async def heartbeat(p,component,detail):
    async with p.acquire() as c:
        await c.execute('''INSERT INTO v6_result_to_draw.worker_status(component,detail) VALUES($1,$2)
          ON CONFLICT(component) DO UPDATE SET heartbeat_at=now(),detail=EXCLUDED.detail''',component,detail)

async def submit(c,snapshot,source='v6_collector',source_id=None,key=None,notify=True):
    error=validate_snapshot(snapshot);job=uuid.uuid4();digest=hashlib.sha256(canonical(snapshot).encode()).hexdigest()
    record=await c.fetchrow('''INSERT INTO v6_result_to_draw.invocations(
      invocation_id,source_invocation_id,idempotency_key,input_sha256,input_values,family,source,
      notify,fixture_id,snapshot_minute,captured_at,status,last_error)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::text::timestamptz,$12,$13)
      ON CONFLICT(idempotency_key) DO NOTHING RETURNING invocation_id''',job,source_id,key or digest,digest,snapshot,FAMILY,source,
      notify,int(snapshot['id']),int(snapshot['snapshot_minute']),str(snapshot['captured_at']),'rejected' if error else 'pending',error)
    if record:
        await c.execute('INSERT INTO v6_result_to_draw.invocation_events(invocation_id,event,detail) VALUES($1,$2,$3)',job,'rejected' if error else 'accepted',{'reason':error,'source_invocation_id':str(source_id) if source_id else None})
        return job,False
    prior=await c.fetchrow('SELECT invocation_id,input_sha256 FROM v6_result_to_draw.invocations WHERE idempotency_key=$1',key or digest)
    if prior['input_sha256']!=digest:raise ValueError('idempotency_conflict')
    return prior['invocation_id'],True

async def collect(p):
    # Read the existing V6 collector's input journal; no second source DB connection.
    async with transaction(p) as c:
        await c.execute('SELECT pg_advisory_xact_lock(610201)')
        pending=await c.fetchval("SELECT count(*) FROM v6_result_to_draw.invocations WHERE status IN ('pending','processing')")
        if pending>=int(bound('DRAW_MAX_BACKLOG',5000,1,100000)):return 0
        rows=await c.fetch('''SELECT i.invocation_id,i.input_values,i.idempotency_key FROM v6.match_centre_invocations i
          WHERE i.source='match_centre_readonly'
          AND i.received_at >= (SELECT (value#>>'{}')::timestamptz FROM v6_result_to_draw.settings WHERE key='feed_started_at')
          AND i.captured_at<=now()
          AND NOT EXISTS(SELECT 1 FROM v6_result_to_draw.invocations d WHERE d.source_invocation_id=i.invocation_id)
          ORDER BY i.received_at,i.invocation_id LIMIT $1''',int(bound('DRAW_FEED_BATCH_SIZE',10,1,25)))
        for r in rows:await submit(c,r['input_values'],source_id=r['invocation_id'],key='v6:'+str(r['invocation_id']))
        return len(rows)

async def claim(p,job_id=None):
    token=uuid.uuid4()
    async with transaction(p) as c:
        await c.execute("UPDATE v6_result_to_draw.invocations SET status='failed',last_error='lease_attempts_exhausted' WHERE status='processing' AND leased_until<now() AND attempts>=5")
        row=await c.fetchrow('''WITH candidate AS(
          SELECT invocation_id FROM v6_result_to_draw.invocations WHERE attempts<5 AND next_attempt_at<=now()
          AND ($1::uuid IS NULL OR invocation_id=$1)
          AND (status='pending' OR(status='processing' AND leased_until<now()))
          ORDER BY received_at FOR UPDATE SKIP LOCKED LIMIT 1)
          UPDATE v6_result_to_draw.invocations j SET status='processing',attempts=attempts+1,
          lease_token=$2,leased_until=now()+interval '60 seconds',updated_at=now()
          FROM candidate c WHERE j.invocation_id=c.invocation_id RETURNING j.*''',job_id,token)
        if row:await c.execute('INSERT INTO v6_result_to_draw.invocation_events(invocation_id,event,detail) VALUES($1,$2,$3)',row['invocation_id'],'processing',{'attempt':row['attempts']})
        return dict(row) if row else None

async def fail(p,job,error,permanent=False):
    async with transaction(p) as c:
        row=await c.fetchrow('''UPDATE v6_result_to_draw.invocations SET status=$3,last_error=$4,
          next_attempt_at=now()+make_interval(secs=>$5),lease_token=NULL,leased_until=NULL,updated_at=now()
          WHERE invocation_id=$1 AND lease_token=$2 AND leased_until>now() RETURNING invocation_id''',
          job['invocation_id'],job['lease_token'],'failed' if permanent or job['attempts']>=5 else 'pending',error[:160],min(300,2**job['attempts']))
        if row:await c.execute('INSERT INTO v6_result_to_draw.invocation_events(invocation_id,event,detail) VALUES($1,$2,$3)',job['invocation_id'],'inference_failed',{'error':error[:160]})

async def mirror(p,job,result):
    async with p.acquire() as c:
        row=await c.fetchrow('SELECT * FROM analytics_v6_result_to_draw.persist_result($1,$2,$3,$4,$5,$6)',
          job['invocation_id'],job['input_values'],result,job['source'],job['notify'],max_age())
        return dict(row)

async def complete(p,job,result,backend):
    async with transaction(p) as c:
        row=await c.fetchrow('''UPDATE v6_result_to_draw.invocations SET status='completed',result=$3,
          engineered_values=$4,model_version=$5,artifact_sha256=$6,probability=$7,threshold=$8,
          eligible=$9,signal=$10,actionable_signal=$11,backend_inference_id=$12,backend_signal_id=$13,
          notification_allowed=$14,suppressed_due_to_prior_signal=$15,superseded=$16,
          completed_at=now(),updated_at=now(),lease_token=NULL,leased_until=NULL,last_error=NULL
          WHERE invocation_id=$1 AND lease_token=$2 AND leased_until>now() RETURNING invocation_id''',
          job['invocation_id'],job['lease_token'],result,result['engineered_features'],result['model_version'],result['artifact_sha256'],
          result['probability'],result['threshold'],result['eligible'],result['signal'],result['actionable_signal'],
          backend['inference_id'],backend['signal_id'],backend['notification_allowed'],backend['suppressed'],backend['superseded'])
        if not row:return False
        await c.execute('INSERT INTO v6_result_to_draw.invocation_events(invocation_id,event,detail) VALUES($1,$2,$3)',job['invocation_id'],'completed',backend)
        if backend['notification_allowed']:
            chat=secret('DRAW_TELEGRAM_CHAT_ID')
            if not chat:raise ValueError('telegram_destination_missing')
            payload={'snapshot':job['input_values'],'prediction':result,'source':job['source'],
              'model_key':MODEL_KEY,'publication_mode':'experimental','backend_inference_id':backend['inference_id'],
              'backend_signal_id':backend['signal_id']}
            await c.execute('''INSERT INTO v6_result_to_draw.telegram_outbox(outbox_id,invocation_id,notification_key,chat_id,payload)
              VALUES($1,$2,$3,$4,$5) ON CONFLICT(notification_key) DO NOTHING''',uuid.uuid4(),job['invocation_id'],backend['notification_key'],chat,payload)
    return True
