import asyncio,sys
from worker.database import pool

async def main(component):
    p=await pool('v6')
    try:
        async with p.acquire() as c:
            ok=await c.fetchval("SELECT heartbeat_at>now()-interval '60 seconds' FROM v6_result_to_draw.worker_status WHERE component=$1",component)
        if ok is not True:raise SystemExit(1)
    finally:await p.close()

if __name__=='__main__':asyncio.run(main(sys.argv[1]))
