import os,json
from pathlib import Path

ASSETS=Path(os.getenv('DRAW_ASSETS','assets'))
REGISTRY=json.loads((ASSETS/'manifest.json').read_text(encoding='utf8'))
TARGET='result_to_draw'
FAMILY=REGISTRY['selected_family']
MODEL_KEY='analytics_v6_result_to_draw_20261011'
MODEL_VERSION=REGISTRY['model_version']
INTEGRATION_VERSION='draw_worker_v1.0.0'
MODEL=REGISTRY['models'][FAMILY]

def secret(name):
    path=os.getenv(name+'_FILE')
    return Path(path).read_text().strip() if path else os.getenv(name,'')

def enabled(name):return os.getenv(name,'false').lower()=='true'

def bound(name,default,low,high):return max(low,min(high,float(os.getenv(name,str(default)))))

def max_age():return int(bound('DRAW_MAX_AGE_SECONDS',300,30,3600))

def model_url():return os.getenv('DRAW_MODEL_URL','http://result-to-draw:8788').rstrip('/')
