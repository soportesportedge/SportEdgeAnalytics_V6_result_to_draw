"""Verify the current V6 backend can call the independent target service."""
from pathlib import Path
import subprocess,json,uuid

ROOT=Path(__file__).resolve().parents[1]

def main():
    request=json.loads((ROOT/'docs/example_request.json').read_text())
    request['request_id']=str(uuid.uuid4())
    key=(ROOT/'secrets/model_api_key.txt').read_text().strip()
    code="import sys,json,httpx; d=json.load(sys.stdin); r=httpx.post('http://result-to-draw:8788/v1/models/result_to_draw/predict',json=d['request'],headers={'Authorization':'Bearer '+d['key']},timeout=20); r.raise_for_status(); print(json.dumps(r.json()))"
    result=subprocess.run(['docker','exec','-i','sportedge-v6-api-1','python','-c',code],input=json.dumps({'key':key,'request':request}),text=True,capture_output=True,check=True)
    data=json.loads(result.stdout)
    assert data['target']=='result_to_draw' and data['request_id']==request['request_id']
    assert data['research_only'] and not data['actionable_signal']
    (ROOT/'outputs/v6_result_to_draw_20261011/backend_network_validation.json').write_text(json.dumps({'caller_container':'sportedge-v6-api-1','url':'http://result-to-draw:8788/v1/models/result_to_draw/predict','pass':True,'result':data},indent=2),encoding='utf8')
    print('PASS: HTTP inference and persisted result called from sportedge-v6-api-1; target',data['target'])

if __name__=='__main__':main()
