"""Causal single-snapshot engineering. No target, team ID, market or future inputs."""
import numpy as np
import pandas as pd

def numeric(s):
    if s.dtype == object or pd.api.types.is_string_dtype(s):
        s=s.replace({'t':1,'f':0,'TRUE':1,'FALSE':0,'true':1,'false':0})
    return pd.to_numeric(s,errors='coerce').astype(float)

def build_features(frame,predictors):
    x={c:numeric(frame[c]) for c in predictors if c not in ['league_id','cluster']}
    for c in ['league_id','cluster']:
        if c in predictors: x[c]=frame[c].astype('string').fillna('__MISSING__').astype(str)
    out=pd.DataFrame(x,index=frame.index)
    catalog=[]; eng={}; stages={0:list(out.columns)}
    def add(name,value,cycle,formula):
        key='eng_'+name; eng[key]=value
        catalog.append({'feature':key,'cycle':cycle,'formula':formula,'null_policy':'propagate source null; impute inside train fold'})
    n=lambda c:out[c]
    minute=n('snapshot_minute').clip(lower=1)
    # Cycle 1: documented pressure, relative strength, efficiency and exposure.
    for side in ['home','away']:
        for stat in ['shots_total','shots_on_target','dangerous_attacks','attacks','corners']:
            add(f'{side}_{stat}_per_min',n(f'{side}_{stat}')/minute,1,f'{side}_{stat}/max(snapshot_minute,1)')
        add(f'{side}_shot_accuracy',n(f'{side}_shots_on_target')/(n(f'{side}_shots_total')+1),1,f'{side}_shots_on_target/({side}_shots_total+1)')
        add(f'{side}_pressure',.5*n(f'{side}_shots_on_target')+.03*n(f'{side}_dangerous_attacks')+.12*n(f'{side}_corners'),1,f'0.5*{side}_shots_on_target+0.03*{side}_dangerous_attacks+0.12*{side}_corners')
        add(f'{side}_attack_efficiency',n(f'{side}_shots_on_target')/(n(f'{side}_dangerous_attacks')+1),1,f'{side}_shots_on_target/({side}_dangerous_attacks+1)')
    for stat in ['shots_total','shots_on_target','dangerous_attacks','attacks','corners','shots_insidebox']:
        add(f'{stat}_share_home',n(f'home_{stat}')/(n(f'home_{stat}')+n(f'away_{stat}')+1),1,f'home_{stat}/(home_{stat}+away_{stat}+1)')
    for key in ['shot_accuracy','pressure','attack_efficiency']:
        add(key+'_diff',eng['eng_home_'+key]-eng['eng_away_'+key],1,f'eng_home_{key}-eng_away_{key}')
    for stat in ['points_per_game','goals_for_per_game','goals_against_per_game','win_rate']:
        add(stat+'_diff',n(stat+'_home')-n(stat+'_away'),1,stat+'_home-'+stat+'_away')
    add('remaining_95',(95-minute).clip(lower=0),1,'max(95-snapshot_minute,0); exposure proxy, not known FT minute')
    add('remaining_100',(100-minute).clip(lower=0),1,'max(100-snapshot_minute,0); exposure proxy')
    add('h2h_home_goal_share',n('h2h_home_goals')/(n('h2h_home_goals')+n('h2h_away_goals')+1),1,'h2h_home_goals/(h2h_home_goals+h2h_away_goals+1)')
    # Orient pressure to the side that must equalize; preserve unknown inputs.
    home_trails=n('dif_score')<0
    def trailing(h,a):return h.where(home_trails,a)
    for stat in ['shots_total','shots_on_target','shots_insidebox','dangerous_attacks','attacks','corners','possession','big_chances_created','big_chances_missed','red_cards']:
        h=n('home_'+stat);a=n('away_'+stat)
        add('trailing_'+stat,trailing(h,a),1,f'{stat} of currently trailing team')
        add('trailing_advantage_'+stat,trailing(h-a,a-h),1,f'trailing {stat} minus leading {stat}')
    add('trailing_pressure',trailing(eng['eng_home_pressure'],eng['eng_away_pressure']),1,'pressure of trailing team')
    add('trailing_strength',trailing(n('points_per_game_home')-n('points_per_game_away'),n('points_per_game_away')-n('points_per_game_home')),1,'PPG trailing minus leading')
    add('h2h_draw_smoothed',(n('h2h_draw')+1)/(n('h2h_total_matches')+3),1,'(h2h_draw+1)/(h2h_total_matches+3)')
    stages[1]=list(out.columns)+list(eng)
    # Cycle 2: acceleration compares disjoint recent 5m and previous 5m.
    for side in ['home','away']:
        for stat in ['shots_total','shots_on_target','shots_insidebox','dangerous_attacks','corners']:
            five=n(f'{side}_{stat}_last_5m'); ten=n(f'{side}_{stat}_last_10m')
            add(f'{side}_{stat}_acceleration',2*five-ten,2,f'2*{side}_{stat}_last_5m-{side}_{stat}_last_10m')
            add(f'{side}_{stat}_recent_vs_match',ten/10-n(f'{side}_{stat}')/minute,2,f'{side}_{stat}_last_10m/10-{side}_{stat}/snapshot_minute')
        for window in [5,10]:
            add(f'{side}_pressure_{window}m',.5*n(f'{side}_shots_on_target_last_{window}m')+.03*n(f'{side}_dangerous_attacks_last_{window}m')+.12*n(f'{side}_corners_last_{window}m'),2,f'0.5*SOT_last_{window}m+0.03*dangerous_attacks_last_{window}m+0.12*corners_last_{window}m ({side})')
        add(f'{side}_box_quality_10m',n(f'{side}_shots_insidebox_last_10m')/(n(f'{side}_shots_total_last_10m')+1),2,f'{side}_shots_insidebox_last_10m/({side}_shots_total_last_10m+1)')
    for window in [5,10]:
        add(f'pressure_diff_{window}m',eng[f'eng_home_pressure_{window}m']-eng[f'eng_away_pressure_{window}m'],2,f'eng_home_pressure_{window}m-eng_away_pressure_{window}m')
        add(f'sot_share_home_{window}m',n(f'home_shots_on_target_last_{window}m')/(n(f'home_shots_on_target_last_{window}m')+n(f'away_shots_on_target_last_{window}m')+1),2,f'home_SOT_last_{window}m/(home_SOT_last_{window}m+away_SOT_last_{window}m+1)')
    for stat in ['shots_total','shots_on_target','shots_insidebox','dangerous_attacks','corners']:
        for window in [5,10]:
            h=n(f'home_{stat}_last_{window}m'); a=n(f'away_{stat}_last_{window}m')
            add(f'trailing_{stat}_{window}m',trailing(h,a),2,f'trailing {stat} last {window}m')
            add(f'trailing_advantage_{stat}_{window}m',trailing(h-a,a-h),2,f'trailing minus leading {stat} last {window}m')
        add(f'trailing_{stat}_acceleration',trailing(eng[f'eng_home_{stat}_acceleration'],eng[f'eng_away_{stat}_acceleration']),2,f'trailing acceleration {stat}')
    add('trailing_pressure_10m',trailing(eng['eng_home_pressure_10m'],eng['eng_away_pressure_10m']),2,'pressure trailing last10m')
    add('leading_pressure_10m',trailing(eng['eng_away_pressure_10m'],eng['eng_home_pressure_10m']),2,'pressure leading last10m')
    stages[2]=list(out.columns)+list(eng)
    # Cycle 3: competing pressure, remaining exposure and match-state interactions.
    for side in ['home','away']:
        pressure=eng[f'eng_{side}_pressure_10m']
        add(f'{side}_pressure_exposure',pressure*eng['eng_remaining_95']/10,3,f'eng_{side}_pressure_10m*eng_remaining_95/10')
        add(f'{side}_losing_pressure',pressure*n(f'is_{side}_losing'),3,f'eng_{side}_pressure_10m*is_{side}_losing')
        add(f'{side}_draw_pressure',pressure*n('is_draw_now'),3,f'eng_{side}_pressure_10m*is_draw_now')
        add(f'{side}_threat',n(f'{side}_shots_insidebox')+.7*n(f'{side}_shots_on_target')+.5*n(f'{side}_big_chances_created'),3,f'{side}_shots_insidebox+0.7*{side}_shots_on_target+0.5*{side}_big_chances_created')
        add(f'{side}_unconverted_chances',n(f'{side}_big_chances_created')-n(f'{side}_score'),3,f'{side}_big_chances_created-{side}_score')
    add('pressure_home_competing',eng['eng_home_pressure_10m']/(eng['eng_home_pressure_10m']+eng['eng_away_pressure_10m']+1),3,'home_pressure_10m/(home_pressure_10m+away_pressure_10m+1)')
    add('threat_diff',eng['eng_home_threat']-eng['eng_away_threat'],3,'eng_home_threat-eng_away_threat')
    add('attack_vs_defence_home',n('goals_for_per_game_home')*n('goals_against_per_game_away'),3,'goals_for_per_game_home*goals_against_per_game_away')
    add('attack_vs_defence_away',n('goals_for_per_game_away')*n('goals_against_per_game_home'),3,'goals_for_per_game_away*goals_against_per_game_home')
    add('red_advantage_home',n('away_red_cards')-n('home_red_cards'),3,'away_red_cards-home_red_cards; NULL is not zero')
    add('substitution_diff_10m',n('home_substitutions_last_10m')-n('away_substitutions_last_10m'),3,'home_substitutions_last_10m-away_substitutions_last_10m')
    remaining=n('announced_minutes_remaining').where(n('announced_minutes_remaining').notna(),eng['eng_remaining_95']).clip(lower=0)
    add('trailing_exposure',eng['eng_trailing_pressure_10m']*remaining/10,3,'trailing pressure * known remaining minutes or fixed95 proxy /10')
    add('leading_exposure',eng['eng_leading_pressure_10m']*remaining/10,3,'leading pressure * remaining /10')
    add('competing_pressure',eng['eng_trailing_pressure_10m']/(eng['eng_leading_pressure_10m']+1),3,'trailing pressure/(leading pressure+1)')
    add('equalization_hazard_proxy',1-np.exp(-eng['eng_trailing_pressure_10m'].clip(lower=0)*remaining/100),3,'1-exp(-trailing pressure*remaining/100); fixed proxy, not fitted probability')
    add('trailing_attack_vs_defence',trailing(n('goals_for_per_game_home')*n('goals_against_per_game_away'),n('goals_for_per_game_away')*n('goals_against_per_game_home')),3,'trailing goals-for rate * leading goals-against rate')
    add('trailing_red_disadvantage',trailing(n('home_red_cards')-n('away_red_cards'),n('away_red_cards')-n('home_red_cards')),3,'red cards trailing minus leading')
    add('trailing_sot_share_10m',trailing(n('home_shots_on_target_last_10m'),n('away_shots_on_target_last_10m'))/(n('home_shots_on_target_last_10m')+n('away_shots_on_target_last_10m')+1),3,'trailing SOT10/(total SOT10+1)')
    add('draw_risk_balance',eng['eng_trailing_pressure_10m']-eng['eng_leading_pressure_10m']*eng['eng_remaining_95']/20,3,'trailing pressure minus leading exposure/20')
    stages[3]=list(out.columns)+list(eng)
    out=pd.concat([out,pd.DataFrame(eng,index=out.index)],axis=1)
    nums=out.select_dtypes('number').columns
    out[nums]=out[nums].replace([np.inf,-np.inf],np.nan)
    return out,stages,catalog
