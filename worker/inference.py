import asyncio,logging,time
import asyncpg,httpx
from worker.config import model_url,secret,enabled,bound,FAMILY
from worker.database import pool
from worker.policy import validate_result
from worker import repository as repo

log=logging.getLogger(__name__)

async def process_one(v6,backend,client,job_id=None):
    job=await repo.claim(v6,job_id)
    if not job:return False
    try:
        response=await client.post(model_url()+'/v1/models/result_to_draw/predict',
          headers={'Authorization':'Bearer '+secret('DRAW_MODEL_API_KEY')},
          json={'snapshot':job['input_values'],'family':FAMILY,'request_id':str(job['invocation_id'])},
          timeout=bound('DRAW_INFERENCE_TIMEOUT',10,1,20))
        if response.status_code!=200:
            await repo.fail(v6,job,'model_http_'+str(response.status_code),response.status_code in (400,401,403,409,422));return True
        result=response.json();validate_result(result,job)
        # Backend commit precedes the V6 outbox. Both stages are idempotent by request UUID.
        mirror=await repo.mirror(backend,job,result)
        await repo.complete(v6,job,result,mirror)
    except (httpx.HTTPError,ValueError,KeyError,TypeError,asyncpg.PostgresError) as exc:
        await repo.fail(v6,job,'inference_'+type(exc).__name__)
        log.warning('Inference attempt retained: %s',type(exc).__name__)
    return True

async def run():
    if not enabled('DRAW_WORKER_ENABLED'):print('result_to_draw worker disabled');return
    v6=await pool('v6');backend=await pool('backend')
    last_collect=0.
    try:
        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=1,max_keepalive_connections=1)) as client:
            while True:
                try:
                    if time.monotonic()-last_collect>=bound('DRAW_FEED_POLL_SECONDS',5,2,60):
                        await repo.collect(v6);last_collect=time.monotonic()
                    busy=await process_one(v6,backend,client)
                    await repo.heartbeat(v6,'inference',{'running':True,'busy':busy})
                    await asyncio.sleep(.1 if busy else bound('DRAW_WORKER_POLL_SECONDS',1,.2,60))
                except Exception as exc:
                    log.error('Worker loop delayed: %s',type(exc).__name__);await asyncio.sleep(5)
    finally:await v6.close();await backend.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING);logging.getLogger('httpcore').setLevel(logging.WARNING)
    asyncio.run(run())
