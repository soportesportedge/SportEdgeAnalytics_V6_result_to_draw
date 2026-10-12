import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
import pandas as pd
from model_core import select_threshold,Calibrator
from feature_engineering import build_features

def test_no_precision_support_abstains():
    y=np.tile([0,0,0,1],20)
    threshold,policy,passed=select_threshold(y,np.full(80,.2),np.arange(80))
    assert threshold>1 and policy=='abstain_no_supported_precision' and not passed

def test_threshold_obeys_precision_first_and_whole_ties():
    y=np.array([1]*20+[0]*10+[1]*10+[0]*40)
    p=np.array([.9]*30+[.7]*10+[.1]*40)
    t,policy,passed=select_threshold(y,p,np.arange(80))
    # At .7 P=75%, R=100% beats .9 P=66.7%, R=66.7%.
    assert t==.7 and policy=='gates' and passed

def test_inverse_calibration_never_reverses_rank():
    raw=np.linspace(.01,.99,80)
    c=Calibrator('sigmoid').fit(raw,np.r_[np.ones(40),np.zeros(40)],np.full(80,80))
    assert c.method=='constant'

def test_engineering_is_single_snapshot_and_trailing_symmetric():
    import json
    root=Path(__file__).resolve().parents[1]
    predictors=json.loads((root/'assets/manifest.json').read_text())['predictors']
    s=json.loads((root/'docs/example_request.json').read_text())['snapshot']
    s['dif_score']=-1;s['home_score']=0;s['away_score']=1
    s['home_shots_on_target_last_10m']=3;s['away_shots_on_target_last_10m']=1
    a=pd.DataFrame([s]);x,_,_=build_features(a,predictors)
    assert x['eng_trailing_shots_on_target_10m'].iloc[0]==3
    s['result_to_draw']=True
    x2,_,_=build_features(pd.DataFrame([s]),predictors)
    pd.testing.assert_frame_equal(x,x2)
