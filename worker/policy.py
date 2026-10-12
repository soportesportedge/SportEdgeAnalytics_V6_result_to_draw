from datetime import datetime,timezone
import math
from worker.config import TARGET,FAMILY,MODEL,MODEL_VERSION,REGISTRY

FUTURE_TARGETS={'result_to_draw','home_score_to_increase','away_score_to_increase','home_result_to_win','away_result_to_win','both_teams_to_score'}

def validate_snapshot(s):
    required=REGISTRY['predictors']+['id','captured_at','state_developer_name']
    missing=[c for c in required if c not in s]
    if missing:return 'missing_fields:'+','.join(missing)
    if any(s.get(c) is not None for c in FUTURE_TARGETS):return 'future_target_present'
    try:
        stamp=datetime.fromisoformat(str(s['captured_at']).replace('Z','+00:00'))
        if stamp.tzinfo is None:return 'timezone_required'
        minute=float(s['snapshot_minute'])
        if not math.isfinite(minute) or not minute.is_integer() or not 0<=minute<=200:return 'invalid_minute'
        if float(s['dif_score'])!=float(s['home_score'])-float(s['away_score']):return 'inconsistent_score'
        for c in ['home_score','away_score']:
            v=float(s[c])
            if not math.isfinite(v) or v<0 or not v.is_integer():return 'invalid_score'
    except (ValueError,TypeError,OverflowError):return 'invalid_snapshot'
    return None

def validate_result(r,job):
    s=job['input_values']
    if r.get('target')!=TARGET or r.get('fixture_id')!=int(s['id']) or r.get('request_id')!=str(job['invocation_id']):raise ValueError('inference_identity_mismatch')
    if r.get('family')!=FAMILY or r.get('model_version')!=MODEL_VERSION or r.get('artifact_sha256')!=MODEL['sha256']:raise ValueError('artifact_contract_mismatch')
    if r.get('captured_at')!=s['captured_at'] or r.get('threshold')!=MODEL['threshold']:raise ValueError('snapshot_threshold_mismatch')
    if r.get('production_approved') is not False or r.get('research_only') is not True or r.get('actionable_signal') is not False:raise ValueError('unexpected_production_approval')
    eligible=s['state_developer_name']=='INPLAY_2ND_HALF' and float(s['snapshot_minute'])>=70 and abs(float(s['dif_score']))==1
    if r.get('eligible') is not eligible:raise ValueError('eligibility_mismatch')
    p=r.get('probability')
    if eligible and (p is None or not math.isfinite(float(p)) or not 0<=float(p)<=1):raise ValueError('invalid_probability')
    if not eligible and p is not None:raise ValueError('probability_outside_eligibility')
    if r.get('signal') is not bool(eligible and p>=r['threshold']):raise ValueError('decision_mismatch')
    if not isinstance(r.get('engineered_features'),dict) or len(r['engineered_features'])!=136:raise ValueError('engineering_contract_mismatch')

def live_fresh(snapshot,age,now=None):
    now=now or datetime.now(timezone.utc)
    try:
        stamp=datetime.fromisoformat(str(snapshot['captured_at']).replace('Z','+00:00'))
        elapsed=(now-stamp).total_seconds()
        return stamp.tzinfo is not None and 0<=elapsed<=age
    except (ValueError,TypeError):return False
