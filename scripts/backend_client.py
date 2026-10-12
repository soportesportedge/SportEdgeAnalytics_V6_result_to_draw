"""Backend integration: call a target-specific endpoint and return its durable record."""
import os,uuid
from pathlib import Path
import httpx

def predict_result_to_draw(snapshot,request_id=None,family=None):
    url=os.getenv('RESULT_TO_DRAW_URL','http://result-to-draw:8788')
    key=Path(os.environ['RESULT_TO_DRAW_API_KEY_FILE']).read_text().strip()
    payload={'snapshot':snapshot,'request_id':str(request_id or uuid.uuid4())}
    if family:payload['family']=family
    r=httpx.post(url+'/v1/models/result_to_draw/predict',headers={'Authorization':'Bearer '+key},json=payload,timeout=15)
    r.raise_for_status()
    return r.json()
