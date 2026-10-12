"""Tests use a newly created Docker PostgreSQL only; no production secrets/roles."""
from pathlib import Path
import json,secrets,subprocess,time

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/worker_integration_20261012'
CONTAINER='sportedge_draw_isolated_test_db'

def run(args,input=None):
    r=subprocess.run(args,input=input,capture_output=True)
    if r.returncode:raise RuntimeError(r.stderr.decode('utf8',errors='replace')[:1800])
    return r.stdout.decode().strip()

def sql(db,statement):
    assert db in ['postgres','draw_test_v6','draw_test_backend']
    return run(['docker','exec','-i',CONTAINER,'psql','-U','postgres','-d',db,'-qAt','-v','ON_ERROR_STOP=1'],statement.encode('utf8'))

def main():
    OUT.mkdir(parents=True,exist_ok=True);password=secrets.token_urlsafe(32)
    envfile=OUT/'test_postgres.env';envfile.write_text('POSTGRES_PASSWORD='+password+'\n',encoding='utf8')
    names=run(['docker','ps','-a','--format','{{.Names}}']).splitlines()
    if CONTAINER in names:raise RuntimeError('Test container already exists; inspect rather than overwrite it')
    run(['docker','run','-d','--rm','--name',CONTAINER,'--network','sportedge_v6','--cpus','0.5','--memory','256m',
      '--pids-limit','128','--tmpfs','/var/lib/postgresql/data:rw,size=192m',
      '--env-file',str(envfile),'-p','127.0.0.1:15438:5432','postgres:16-alpine'])
    for _ in range(20):
        r=subprocess.run(['docker','exec',CONTAINER,'pg_isready','-U','postgres'],capture_output=True)
        if r.returncode==0:break
        time.sleep(.5)
    else:raise RuntimeError('Test PostgreSQL did not become ready')
    sql('postgres','CREATE DATABASE draw_test_v6; CREATE DATABASE draw_test_backend;')
    # Both users exist only in this disposable cluster and have new test credentials.
    sql('postgres',f"CREATE ROLE draw_test_v6_worker LOGIN PASSWORD '{password}'; CREATE ROLE draw_test_backend_writer LOGIN PASSWORD '{password}';")
    sql('draw_test_v6',"CREATE SCHEMA v6; CREATE TABLE v6.match_centre_invocations(invocation_id uuid PRIMARY KEY,idempotency_key text,input_values jsonb,source text,received_at timestamptz DEFAULT now(),captured_at timestamptz);")
    sql('draw_test_v6',(ROOT/'sql/001_draw_queue.sql').read_text(encoding='utf8'))
    sql('draw_test_v6',"GRANT USAGE ON SCHEMA v6,v6_result_to_draw TO draw_test_v6_worker; GRANT SELECT ON v6.match_centre_invocations TO draw_test_v6_worker; GRANT SELECT ON v6_result_to_draw.settings TO draw_test_v6_worker; GRANT SELECT,INSERT,UPDATE ON v6_result_to_draw.invocations,v6_result_to_draw.invocation_events,v6_result_to_draw.telegram_outbox,v6_result_to_draw.worker_status TO draw_test_v6_worker; GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA v6_result_to_draw TO draw_test_v6_worker;")
    sql('draw_test_backend',(ROOT/'sql/test_legacy_tables.sql').read_text(encoding='utf8'))
    registry=json.loads((ROOT/'assets/manifest.json').read_text())
    script=(ROOT/'sql/002_backend_bridge.sql').read_text(encoding='utf8').replace('__ARTIFACT_SHA256__',registry['models'][registry['selected_family']]['sha256'])
    sql('draw_test_backend',script)
    sql('draw_test_backend',"GRANT USAGE ON SCHEMA analytics_v6_result_to_draw TO draw_test_backend_writer; GRANT EXECUTE ON FUNCTION analytics_v6_result_to_draw.persist_result(uuid,jsonb,jsonb,text,boolean,integer) TO draw_test_backend_writer; GRANT EXECUTE ON FUNCTION analytics_v6_result_to_draw.record_delivery(uuid,text,timestamptz,jsonb) TO draw_test_backend_writer;")
    (OUT/'test_password.txt').write_text(password,encoding='utf8')
    envfile.unlink() # It is no longer needed after container initialization.
    print('Disposable test PostgreSQL ready; no production role or credential modified')

if __name__=='__main__':main()
