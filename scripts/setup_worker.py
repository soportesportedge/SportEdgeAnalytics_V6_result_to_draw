"""Provision target-scoped roles and schemas. Passwords never enter argv/logs."""
from pathlib import Path
from datetime import datetime,timezone
import subprocess,json,secrets,re,argparse

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/worker_integration_20261012'
OLD=Path(r'C:\Proyectos\SportEdgeAnalytics_V6_Home_to_Increase\deployments\SportEdgeIntegracionAnalytics_V6')
V6_CONTAINER='sportedge-v6-v6-db-1';BACKEND_CONTAINER='sportedge_v2_db'
V6_USER='v6';BACKEND_USER='sportedge'
V6_DB='sportedge_v6';BACKEND_DB='sportedge_v2'
V6_TEST='sportedge_draw_worker_test';BACKEND_TEST='sportedge_draw_backend_test'

def sql(container,user,db,statement):
    r=subprocess.run(['docker','exec','-i',container,'psql','-U',user,'-d',db,'-qAt','-v','ON_ERROR_STOP=1'],input=statement.encode('utf8'),capture_output=True)
    if r.returncode:
        error=r.stderr.decode('utf8',errors='replace')
        for p in (ROOT/'secrets').glob('*password*'):
            error=error.replace(p.read_text().strip(),'[redacted]')
        raise RuntimeError(error[:1800])
    return r.stdout.decode('utf8').strip()

def ensure_database(container,user,name):
    if not re.fullmatch('[a-z0-9_]+',name):raise ValueError('database identifier')
    exists=sql(container,user,'postgres',f"SELECT 1 FROM pg_database WHERE datname='{name}'")
    if not exists:sql(container,user,'postgres',f'CREATE DATABASE {name}')

def init_secrets():
    root=ROOT/'secrets';root.mkdir(exist_ok=True)
    for name in ['draw_v6_db_password','draw_backend_db_password']:
        p=root/(name+'.txt')
        if not p.exists():p.write_text(secrets.token_urlsafe(48),encoding='utf8')
    assert (OLD/'secrets/telegram_chat_id.txt').read_text().strip()=='-5538403637','Authorized destination mismatch'

def role(container,user,role_name,password,limit):
    assert re.fullmatch('[a-z0-9_]+',role_name) and re.fullmatch('[A-Za-z0-9_-]+',password)
    marker='Managed by SportEdge result_to_draw worker'
    sql(container,user,'postgres',f"""DO $body$ BEGIN
      IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role_name}') THEN
        CREATE ROLE {role_name} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT CONNECTION LIMIT {limit};
        COMMENT ON ROLE {role_name} IS '{marker}';
      ELSIF (SELECT shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname='{role_name}') IS DISTINCT FROM '{marker}' THEN
        RAISE EXCEPTION 'Existing role is not owned by this integration';
      END IF; END $body$;
      ALTER ROLE {role_name} PASSWORD '{password}';
      ALTER ROLE {role_name} SET statement_timeout='5000ms';
      ALTER ROLE {role_name} SET lock_timeout='500ms';
      ALTER ROLE {role_name} SET idle_in_transaction_session_timeout='10000ms';""")

def legacy_ddl():
    p=subprocess.run(['docker','exec',BACKEND_CONTAINER,'pg_dump','-U',BACKEND_USER,'-d',BACKEND_DB,'--schema-only','--no-owner','--no-privileges',
      '-t','public.adjusted_analytic_inferences','-t','public.adjusted_analytic_signals','-t','public.telegram_alert_outbox'],capture_output=True,check=True)
    return p.stdout

def bridge_script():
    registry=json.loads((ROOT/'assets/manifest.json').read_text())
    digest=registry['models'][registry['selected_family']]['sha256']
    return (ROOT/'sql/002_backend_bridge.sql').read_text(encoding='utf8').replace('__ARTIFACT_SHA256__',digest)

def install(v6_db,backend_db):
    sql(V6_CONTAINER,V6_USER,v6_db,(ROOT/'sql/001_draw_queue.sql').read_text(encoding='utf8'))
    sql(BACKEND_CONTAINER,BACKEND_USER,backend_db,bridge_script())
    sql(V6_CONTAINER,V6_USER,v6_db,f'''GRANT CONNECT ON DATABASE {v6_db} TO sportedge_v6_draw;
      GRANT USAGE ON SCHEMA v6,v6_result_to_draw TO sportedge_v6_draw;
      GRANT SELECT ON v6.match_centre_invocations TO sportedge_v6_draw;
      GRANT SELECT ON v6_result_to_draw.settings TO sportedge_v6_draw;
      GRANT SELECT,INSERT,UPDATE ON v6_result_to_draw.invocations,v6_result_to_draw.invocation_events,
        v6_result_to_draw.telegram_outbox,v6_result_to_draw.worker_status TO sportedge_v6_draw;
      GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA v6_result_to_draw TO sportedge_v6_draw;''')
    links=sql(V6_CONTAINER,V6_USER,v6_db,"SELECT to_regclass('v6.fixture_link_registry') IS NOT NULL")
    if links=='t':sql(V6_CONTAINER,V6_USER,v6_db,'GRANT SELECT ON v6.fixture_link_registry TO sportedge_v6_draw')
    sql(BACKEND_CONTAINER,BACKEND_USER,backend_db,f'''GRANT CONNECT ON DATABASE {backend_db} TO sportedge_draw_writer;
      GRANT USAGE ON SCHEMA analytics_v6_result_to_draw TO sportedge_draw_writer;
      GRANT EXECUTE ON FUNCTION analytics_v6_result_to_draw.persist_result(uuid,jsonb,jsonb,text,boolean,integer) TO sportedge_draw_writer;
      GRANT EXECUTE ON FUNCTION analytics_v6_result_to_draw.record_delivery(uuid,text,timestamptz,jsonb) TO sportedge_draw_writer;''')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['production'],required=True);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    for filename in ['isolated_tests.json','isolated_real_model_validation.json']:
        evidence=OUT/filename
        assert evidence.exists() and json.loads(evidence.read_text(encoding='utf8'))['passed'],'Run isolated integration and real-model tests before production installation'
    ddl=legacy_ddl();(OUT/'backend_original_tables_schema.sql').write_bytes(ddl)
    init_secrets()
    role(V6_CONTAINER,V6_USER,'sportedge_v6_draw',(ROOT/'secrets/draw_v6_db_password.txt').read_text().strip(),6)
    role(BACKEND_CONTAINER,BACKEND_USER,'sportedge_draw_writer',(ROOT/'secrets/draw_backend_db_password.txt').read_text().strip(),4)
    install(V6_DB,BACKEND_DB)
    (OUT/'production_schema_installation.json').write_text(json.dumps({'installed_at':datetime.now(timezone.utc).isoformat(),
          'v6_schema':'v6_result_to_draw','backend_schema':'analytics_v6_result_to_draw',
          'legacy_tables':['adjusted_analytic_inferences','adjusted_analytic_signals','telegram_alert_outbox'],
          'legacy_schema_altered':False,'writer_direct_table_access':False,'telegram_chat_id':'-5538403637'},indent=2),encoding='utf8')
    print(args.mode,'schemas and scoped roles installed; credentials not printed')

if __name__=='__main__':main()
