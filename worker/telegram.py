"""Target-specific experimental alerts; timeout/unknown delivery never auto-resends."""
import asyncio,html,logging,math,uuid
import asyncpg,httpx
from worker.config import enabled,secret,max_age,bound
from worker.database import pool,transaction
from worker.repository import heartbeat
from worker.fixture_links import cached_links,valid_event_url

log=logging.getLogger(__name__)

def display(v,limit=120):return html.escape(str(v if v is not None else '-')[:limit])

def format_message(payload):
    s,r=payload['snapshot'],payload['prediction']
    home=display(s.get('home_team_name') or 'Local');away=display(s.get('away_team_name') or 'Visitante')
    lines=['<b>🧪 SPORTEDGE V6 · EMPATE</b>','',
      '⚽ <b>Posible igualación y empate al cierre</b>',
      f'🏟️ <b>{home}</b> {display(s.get("home_score"))} - {display(s.get("away_score"))} <b>{away}</b>',
      f'⏱️ Minuto <b>{display(s.get("snapshot_minute"))}</b>']
    if s.get('league_name'):lines.append('🏆 '+display(s['league_name']))
    p=r.get('probability')
    if p is not None and math.isfinite(float(p)):lines+=['',f'📊 Estimación experimental: <b>{float(p)*100:.1f}%</b>',f'Umbral del modelo: {float(r["threshold"])*100:.1f}%']
    for provider,label in [('365scores','📱 Ver partido en 365Scores'),('betano','🎯 Ver partido en Betano')]:
        link=payload.get('fixture_links',{}).get(provider,{})
        if valid_event_url(provider,link.get('url'),link.get('event_id')):lines+=['',f'<a href="{html.escape(link["url"],quote=True)}">{label}</a>']
    lines+=['','<i>Modelo result_to_draw · investigación.</i>',
      'Prueba independiente: precisión 30,2% y recall 11,0%. No cumple los requisitos de producción.']
    if payload.get('source')=='technical_test':
        lines[0]='<b>🧪 PRUEBA TÉCNICA V6 · RESULT_TO_DRAW</b>'
        lines.insert(2,'Ejemplo histórico para verificar el circuito. No es una señal en vivo.')
    return '\n'.join(lines)

async def claim(p):
    token=uuid.uuid4()
    async with transaction(p) as c:
        await c.execute("UPDATE v6_result_to_draw.telegram_outbox SET status='uncertain',last_error='expired_delivery_lease' WHERE status='sending' AND leased_until<now()")
        await c.execute('''UPDATE v6_result_to_draw.telegram_outbox SET status='suppressed',last_error='stale_notification'
          WHERE status='pending' AND (created_at<now()-make_interval(secs=>$1)
          OR(payload->>'source' <> 'technical_test' AND(payload->'snapshot'->>'captured_at')::timestamptz<now()-make_interval(secs=>$1)))''',max_age())
        r=await c.fetchrow('''WITH candidate AS(
          SELECT outbox_id FROM v6_result_to_draw.telegram_outbox WHERE status='pending' AND attempts<5 AND next_attempt_at<=now()
          ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
          UPDATE v6_result_to_draw.telegram_outbox o SET status='sending',attempts=attempts+1,
          lease_token=$1,leased_until=now()+interval '60 seconds'
          FROM candidate c WHERE o.outbox_id=c.outbox_id RETURNING o.*''',token)
        if not r:return None
        payload=dict(r['payload']);links={}
        try:
            async with c.transaction():links=await cached_links(c,payload['snapshot'])
        except Exception as exc:log.warning('Fixture links omitted: %s',type(exc).__name__)
        payload['fixture_links']=links;payload['chat_id']=r['chat_id']
        await c.execute('UPDATE v6_result_to_draw.telegram_outbox SET payload=$2 WHERE outbox_id=$1',r['outbox_id'],payload)
        return dict(r)|{'payload':payload}

async def finish(p,row,status,message_id=None,error=None,delay=5):
    async with transaction(p) as c:
        await c.execute('''UPDATE v6_result_to_draw.telegram_outbox SET status=$3,telegram_message_id=$4,last_error=$5,
          next_attempt_at=now()+make_interval(secs=>$6),sent_at=CASE WHEN $3='sent' THEN now() ELSE sent_at END,
          lease_token=NULL,leased_until=NULL WHERE outbox_id=$1 AND lease_token=$2 AND leased_until>now()''',
          row['outbox_id'],row['lease_token'],status,message_id,error,int(delay))

async def deliver_one(v6,client):
    if not enabled('DRAW_TELEGRAM_ENABLED'):return False
    row=await claim(v6)
    if not row:return False
    if not enabled('DRAW_TELEGRAM_ENABLED'):
        await finish(v6,row,'pending',error='delivery_disabled');return False
    token=secret('DRAW_TELEGRAM_BOT_TOKEN');chat=secret('DRAW_TELEGRAM_CHAT_ID')
    if not token or row['chat_id']!=chat:
        await finish(v6,row,'failed',error='telegram_destination_mismatch');return True
    try:
        response=await client.post('https://api.telegram.org/bot'+token+'/sendMessage',
          json={'chat_id':row['chat_id'],'text':format_message(row['payload']),'parse_mode':'HTML','link_preview_options':{'is_disabled':True}},timeout=10)
        body=response.json()
        if not isinstance(body,dict):raise ValueError('unexpected_response')
        message=body.get('result',{})
        if response.status_code==200 and body.get('ok') is True and isinstance(message,dict) and isinstance(message.get('message_id'),int):
            await finish(v6,row,'sent',str(message['message_id']))
        elif response.status_code==429:
            delay=max(1,min(3600,int(body.get('parameters',{}).get('retry_after',30))))
            await finish(v6,row,'failed' if row['attempts']>=5 else 'pending',error='telegram_rate_limit',delay=delay)
        elif 400<=response.status_code<500:await finish(v6,row,'failed',error='telegram_http_'+str(response.status_code))
        else:await finish(v6,row,'uncertain',error='telegram_unconfirmed_response')
    except (httpx.ConnectError,httpx.ConnectTimeout):
        await finish(v6,row,'failed' if row['attempts']>=5 else 'pending',error='telegram_connection_failed',delay=2**row['attempts'])
    except (httpx.HTTPError,ValueError,KeyError,TypeError):await finish(v6,row,'uncertain',error='telegram_delivery_unconfirmed')
    return True

async def mirror_sent(v6,backend):
    async with v6.acquire() as c:
        row=await c.fetchrow('''SELECT * FROM v6_result_to_draw.telegram_outbox WHERE status='sent' AND NOT backend_mirrored
          AND backend_mirror_next_attempt_at<=now() ORDER BY sent_at LIMIT 1''')
    if not row:return False
    try:
        async with backend.acquire() as c:
            legacy_id=await c.fetchval('SELECT analytics_v6_result_to_draw.record_delivery($1,$2,$3,$4)',
              row['invocation_id'],row['telegram_message_id'],row['sent_at'],row['payload'])
        async with v6.acquire() as c:
            await c.execute('UPDATE v6_result_to_draw.telegram_outbox SET backend_mirrored=true,backend_outbox_id=$2 WHERE outbox_id=$1',row['outbox_id'],legacy_id)
    except asyncpg.PostgresError as exc:
        async with v6.acquire() as c:
            await c.execute('''UPDATE v6_result_to_draw.telegram_outbox SET backend_mirror_attempts=backend_mirror_attempts+1,
              backend_mirror_next_attempt_at=now()+make_interval(secs=>$2) WHERE outbox_id=$1''',row['outbox_id'],min(300,2**min(row['backend_mirror_attempts']+1,8)))
        log.warning('Delivery mirror retained: %s',type(exc).__name__)
    return True

async def run():
    v6=await pool('v6');backend=await pool('backend')
    try:
        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=1,max_keepalive_connections=1)) as client:
            while True:
                try:
                    await mirror_sent(v6,backend)
                    busy=await deliver_one(v6,client)
                    await heartbeat(v6,'telegram',{'running':True,'enabled':enabled('DRAW_TELEGRAM_ENABLED'),'busy':busy})
                    await asyncio.sleep(1.2 if busy else 2)
                except Exception as exc:log.error('Telegram loop delayed: %s',type(exc).__name__);await asyncio.sleep(5)
    finally:await v6.close();await backend.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING);logging.getLogger('httpcore').setLevel(logging.WARNING)
    asyncio.run(run())
