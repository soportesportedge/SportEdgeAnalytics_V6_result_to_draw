import json,os
from contextlib import asynccontextmanager
import asyncpg
from worker.config import secret,bound

async def init(c):
    await c.set_type_codec('jsonb',schema='pg_catalog',encoder=json.dumps,decoder=json.loads,format='text')

async def pool(kind='v6'):
    prefix='DRAW_V6' if kind=='v6' else 'DRAW_BACKEND'
    return await asyncpg.create_pool(os.environ[prefix+'_DATABASE_URL'],password=secret(prefix+'_DB_PASSWORD'),
        min_size=1,max_size=int(bound(prefix+'_POOL_SIZE',2 if kind=='v6' else 1,1,3)),command_timeout=8,
        server_settings={'application_name':'sportedge_result_to_draw_'+kind,'statement_timeout':'5000','lock_timeout':'500','idle_in_transaction_session_timeout':'10000'},init=init)

@asynccontextmanager
async def transaction(p):
    async with p.acquire() as c,c.transaction():yield c
