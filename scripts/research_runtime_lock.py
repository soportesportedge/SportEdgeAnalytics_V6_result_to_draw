from pathlib import Path
import importlib.metadata as m,json,sys
root=Path(__file__).resolve().parents[1]
packages=['numpy','pandas','scipy','scikit-learn','xgboost','catboost','joblib','fastapi','uvicorn','httpx','threadpoolctl','matplotlib','pytest']
data={p:m.version(p) for p in packages};data['python']=sys.version
(root/'outputs/v6_result_to_draw_20261011/runtime_training_lock.json').write_text(json.dumps(data,indent=2),encoding='utf8')
(root/'requirements-research.txt').write_text('\n'.join(p+'=='+data[p] for p in packages)+'\n',encoding='utf8')
print('Research dependencies recorded')
