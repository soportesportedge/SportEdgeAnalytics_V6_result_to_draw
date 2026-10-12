"""Create a draft PR with cached Git authentication; never print credentials."""
from pathlib import Path
import os,subprocess,json,ssl
import httpx

ROOT=Path(__file__).resolve().parents[1]
REPO='soportesportedge/SportEdgeAnalytics_V6_result_to_draw'
HEAD='codex/result-to-draw-worker'

def main():
    env=dict(os.environ);env['GIT_TERMINAL_PROMPT']='0'
    credential=subprocess.run(['git','-c','credential.interactive=false','credential','fill'],
      input='protocol=https\nhost=github.com\n\n',text=True,capture_output=True,env=env,check=True)
    values=dict(line.split('=',1) for line in credential.stdout.splitlines() if '=' in line)
    password=values.get('password')
    if not password:raise SystemExit('Cached GitHub credential unavailable; no PR created')
    body='''Los snapshots capturados por V6 ahora se procesan automáticamente con un worker exclusivo de result_to_draw. Los resultados se registran en adjusted_analytic_inferences y adjusted_analytic_signals con una clave propia, y las notificaciones experimentales se entregan al grupo confirmado SportEdge_Latam_Reloaded.

La cola V6 conserva cada captura, UUID, leases y reintentos. La escritura en el backend ocurre antes de crear el outbox. El rol escritor sólo ejecuta funciones fijadas al target, versión y hash; las tablas compartidas mantienen su estructura. Los envíos confirmados se reflejan como sent en telegram_alert_outbox, sin competir con el worker antiguo. Los timeouts sin confirmación quedan en uncertain.

El modelo mantiene production_approved=false y actionable_signal=false. La publicación experimental fue solicitada por el usuario y los mensajes muestran su condición de investigación.

Validación: 32 comprobaciones con PostgreSQL temporal y Telegram simulado, cuatro pruebas de política, circuito con modelo real por HTTP y una prueba productiva histórica identificada como técnica. Telegram confirmó el mensaje 3194; se reconciliaron inferencia 21990, señal 109682 y outbox 1764. Los tres contenedores están saludables y los workers no tuvieron reinicios. Los contenedores de prueba se retiraron.
'''
    with httpx.Client(base_url='https://api.github.com',verify=ssl.create_default_context(),timeout=30,
      headers={'Authorization':'Bearer '+password,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}) as c:
        existing=c.get('/repos/'+REPO+'/pulls',params={'state':'open','head':'soportesportedge:'+HEAD})
        if existing.status_code!=200:raise SystemExit('GitHub lookup failed with HTTP '+str(existing.status_code))
        prs=existing.json()
        if prs:url=prs[0]['html_url']
        else:
            r=c.post('/repos/'+REPO+'/pulls',json={'title':'Add V6 result_to_draw worker and shared analytics persistence','head':HEAD,'base':'main','body':body,'draft':True})
            if r.status_code!=201:raise SystemExit('GitHub PR creation failed with HTTP '+str(r.status_code))
            url=r.json()['html_url']
    (ROOT/'outputs/worker_integration_20261012/pull_request.json').write_text(json.dumps({'url':url,'draft':True},indent=2),encoding='utf8')
    print(url)

if __name__=='__main__':main()
