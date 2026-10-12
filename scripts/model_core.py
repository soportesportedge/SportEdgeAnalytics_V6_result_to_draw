"""Reusable training/serving core. No data or future-label dependencies on import."""
import os
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
import numpy as np
import pandas as pd
from scipy.special import logit
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier, HistGradientBoostingClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss, log_loss, confusion_matrix

SEED=20261011
FAMILIES=['logistic','extra_trees','random_forest','hist_gradient_boosting','gradient_boosting','xgboost','catboost']
METHODS=['identity','sigmoid','isotonic','beta','sigmoid_minute']

def weights(d,y=None,balance=0):
    # A fixture's aggregate weight is invariant to its number of snapshots.
    w=1/d.groupby('id').id.transform('size').to_numpy(float)
    w/=w.mean()
    if balance and y is not None:
        rate=np.average(y,weights=w)
        w*=np.where(y==1,(.5/max(rate,1e-6))**balance,(.5/max(1-rate,1e-6))**balance)
    return w/w.mean()

def feature_ranking(x,y,d):
    nums=x.select_dtypes('number').columns.tolist()
    missing=x[nums].isna().mean();unique=x[nums].nunique()
    good=[c for c in nums if missing[c]<.98 and unique[c]>1]
    median=x[good].median().fillna(0)
    z=x[good].fillna(median)
    picks=[]
    for _,g in d.groupby('id',sort=False):
        picks.extend(g.index[np.unique(np.linspace(0,len(g)-1,min(6,len(g))).astype(int))])
    pos=d.index.get_indexer(picks)
    sub=z.iloc[pos];yy=np.asarray(y)[pos]
    mi=mutual_info_classif(sub,yy,random_state=SEED,n_neighbors=5)
    proxy=ExtraTreesClassifier(n_estimators=100,max_depth=7,min_samples_leaf=12,max_features=.65,n_jobs=4,random_state=SEED)
    proxy.fit(z,y,sample_weight=weights(d,y,.5))
    rho=sub.corrwith(pd.Series(yy,index=sub.index),method='spearman').fillna(0)
    score=.35*pd.Series(mi,index=good).rank(pct=True)+.45*pd.Series(proxy.feature_importances_,index=good).rank(pct=True)+.2*rho.abs().rank(pct=True)
    corr=sub.corr(method='spearman').abs().fillna(0)
    order=[];decisions=[]
    for c in score.sort_values(ascending=False).index:
        conflicts=[q for q in order if corr.loc[c,q]>.995]
        reason='ranked' if not conflicts else 'redundant:'+conflicts[0]
        if not conflicts:order.append(c)
        decisions.append({'feature':c,'rank_score':float(score[c]),'mutual_information':float(mi[good.index(c)]),'spearman_target':float(rho[c]),'proxy_importance':float(proxy.feature_importances_[good.index(c)]),'missing_train':float(missing[c]),'decision':reason})
    for c in nums:
        if c not in good: decisions.append({'feature':c,'missing_train':float(missing[c]),'decision':'missing_ge98pct' if missing[c]>=.98 else 'constant'})
    return {'order':order,'decisions':decisions,'median':median,'missing':missing,'corr':corr}

class FeatureTransform:
    def __init__(self,limit):self.limit=limit
    def fit(self,x,ranking):
        self.selected=ranking['order'][:self.limit]
        self.median=ranking['median'][self.selected]
        self.flags=[c for c in self.selected if .02<ranking['missing'][c]<.98]
        self.cats=[c for c in ['league_id','cluster'] if c in x]
        self.encoder=OneHotEncoder(handle_unknown='ignore',sparse_output=False,min_frequency=8,dtype=np.float32)
        if self.cats:self.encoder.fit(x[self.cats])
        self.scaler=StandardScaler().fit(self.matrix(x))
        self.names=self.selected+['missing__'+c for c in self.flags]+(self.encoder.get_feature_names_out(self.cats).tolist() if self.cats else [])
        return self
    def matrix(self,x):
        parts=[x[self.selected].fillna(self.median).to_numpy(np.float32)]
        if self.flags:parts.append(x[self.flags].isna().to_numpy(np.float32))
        if self.cats:parts.append(self.encoder.transform(x[self.cats]))
        return np.concatenate(parts,axis=1)
    def transform(self,x):return self.scaler.transform(self.matrix(x)).astype(np.float32)

def estimator(family,p):
    if family=='logistic':return LogisticRegression(max_iter=800,solver='lbfgs',**p)
    if family=='extra_trees':return ExtraTreesClassifier(random_state=SEED,n_jobs=4,**p)
    if family=='random_forest':return RandomForestClassifier(random_state=SEED,n_jobs=4,**p)
    if family=='hist_gradient_boosting':return HistGradientBoostingClassifier(random_state=SEED,early_stopping=False,**p)
    if family=='gradient_boosting':return GradientBoostingClassifier(random_state=SEED,**p)
    if family=='xgboost':
        from xgboost import XGBClassifier
        return XGBClassifier(random_state=SEED,n_jobs=4,tree_method='hist',eval_metric='logloss',**p)
    if family=='catboost':
        from catboost import CatBoostClassifier
        return CatBoostClassifier(random_seed=SEED,thread_count=4,verbose=False,allow_writing_files=False,loss_function='Logloss',**p)
    raise ValueError(family)

def configs(family,n=40):
    rng=np.random.default_rng(SEED+FAMILIES.index(family))
    pick=lambda a:a[int(rng.integers(len(a)))]
    result=[]
    for i in range(n):
        cycle=i%4;limit=pick([35,70,120]);balance=pick([0,.5,1])
        if i<4:limit=70;balance=.5
        if family=='logistic':p={'C':float(10**rng.uniform(-3,1.2))}
        elif family in ['random_forest','extra_trees']:p={'n_estimators':pick([100,180,280]),'max_depth':pick([4,6,9,14,None]),'min_samples_leaf':pick([6,15,30,60]),'max_features':pick([.35,.65,1.])}
        elif family=='hist_gradient_boosting':p={'max_iter':pick([80,140,240]),'learning_rate':pick([.025,.05,.09]),'max_leaf_nodes':pick([5,9,15,25]),'min_samples_leaf':pick([25,50,80]),'l2_regularization':pick([1.,5.,20.])}
        elif family=='gradient_boosting':p={'n_estimators':pick([70,120,180]),'learning_rate':pick([.025,.05,.09]),'max_depth':pick([1,2,3]),'min_samples_leaf':pick([20,45,80]),'subsample':pick([.7,1.]),'max_features':pick([.5,.8])}
        elif family=='xgboost':p={'n_estimators':pick([100,180,280]),'learning_rate':pick([.025,.05,.09]),'max_depth':pick([2,3,4]),'min_child_weight':pick([10,25,50]),'subsample':pick([.7,1.]),'colsample_bytree':pick([.5,.85]),'reg_lambda':pick([3.,10.,30.])}
        else:p={'iterations':pick([100,180,280]),'depth':pick([3,4,6]),'learning_rate':pick([.025,.05,.09]),'l2_leaf_reg':pick([3.,10.,30.]),'random_strength':pick([.5,2.]),'border_count':64}
        result.append({'base_id':f'{family}_{i+1:03d}','family':family,'cycle':cycle,'limit':limit,'balance':balance,'params':p})
    return result

class Calibrator:
    def __init__(self,method):self.method=method;self.requested_method=method
    def design(self,p,minute):
        p=np.clip(p,1e-5,1-1e-5)
        z=np.column_stack([np.log(p),-np.log1p(-p)]) if self.method=='beta' else logit(p).reshape(-1,1)
        if self.method=='sigmoid_minute':z=np.column_stack([z,(np.asarray(minute)-80)/10])
        return z
    def fit(self,p,y,minute):
        if self.method=='identity':return self
        self.constant=float(np.mean(y))
        if np.unique(p).size<4 or np.unique(y).size<2:self.method='constant';return self
        if self.method=='isotonic':
            self.model=IsotonicRegression(y_min=1e-5,y_max=1-1e-5,out_of_bounds='clip').fit(p,y)
        else:
            self.model=LogisticRegression(C=1.,max_iter=500).fit(self.design(p,minute),y)
            if self.model.coef_[0,0]<=0 or (self.method=='beta' and np.any(self.model.coef_[0]<0)):self.method='constant'
        return self
    def predict(self,p,minute):
        if self.method=='identity':return np.asarray(p)
        if self.method=='constant':return np.full(len(p),self.constant)
        if self.method=='isotonic':return self.model.predict(p)
        return self.model.predict_proba(self.design(p,minute))[:,1]

def select_threshold(y,p,ids,min_rows=30,min_fixtures=10):
    # Evaluate whole tie groups. Prefer precision; recall is the only relaxable gate.
    y=np.asarray(y,int);p=np.asarray(p,float);ids=np.asarray(ids)
    order=np.argsort(-p,kind='stable');ps=p[order];ys=y[order]
    tp=np.cumsum(ys);support=np.arange(1,len(p)+1)
    seen=set();nf=[]
    for i in order:seen.add(ids[i]);nf.append(len(seen))
    end=np.r_[ps[:-1]!=ps[1:],True]
    precision=tp/support;recall=tp/max(y.sum(),1)
    support_ok=end&(support>=min_rows)&(np.array(nf)>=min_fixtures)
    gate=support_ok&(precision>.5)&(recall>.25)
    floor=support_ok&(precision>.5)
    mask=gate if gate.any() else floor
    if not mask.any():return 1.000001,'abstain_no_supported_precision',False
    candidates=np.where(mask)[0]
    if gate.any():ix=max(candidates,key=lambda k:(precision[k],recall[k],support[k]))
    else:ix=max(candidates,key=lambda k:(recall[k],precision[k],support[k]))
    return float(ps[ix]),'gates' if gate.any() else 'precision_preserved_recall_relaxed',bool(gate.any())

def metrics(y,p,t=None,pred=None,sample_weight=None):
    y=np.asarray(y,int);p=np.asarray(p,float)
    pred=np.asarray(pred,bool) if pred is not None else p>=t
    tn,fp,fn,tp=confusion_matrix(y,pred,labels=[0,1],sample_weight=sample_weight).ravel()
    precision=tp/(tp+fp) if tp+fp else 0.;recall=tp/(tp+fn) if tp+fn else 0.
    w=np.ones(len(y)) if sample_weight is None else np.asarray(sample_weight)
    bins=np.clip(np.floor(p*10),0,9).astype(int)
    ece=sum(w[bins==b].sum()/w.sum()*abs(np.average(y[bins==b],weights=w[bins==b])-np.average(p[bins==b],weights=w[bins==b])) for b in range(10) if np.any(bins==b))
    return {'rows':len(y),'positives':int(y.sum()),'tp':float(tp),'fp':float(fp),'fn':float(fn),'tn':float(tn),'precision':float(precision),'recall':float(recall),'f1':float(2*precision*recall/(precision+recall)) if precision+recall else 0.,'gates_pass':bool(precision>.5 and recall>.25),'threshold':float(t) if t is not None else None,'ap':float(average_precision_score(y,p,sample_weight=sample_weight)),'roc_auc':float(roc_auc_score(y,p,sample_weight=sample_weight)) if len(set(y))==2 else None,'brier':float(brier_score_loss(y,p,sample_weight=sample_weight)),'log_loss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),sample_weight=sample_weight,labels=[0,1])),'ece_10':float(ece),'probability_mean':float(np.average(p,weights=w)),'probability_signal_mean':float(np.average(p[pred],weights=w[pred])) if np.any(pred) else None,'signal_rate':float(np.average(pred,weights=w)),'prevalence':float(np.average(y,weights=w))}

def rank(m):
    return (int(m['gates_pass']),int(m['precision']>.5),m['precision'] if m['gates_pass'] else m['recall'] if m['precision']>.5 else 0.,m['ap'],-m['brier'])

def predict(b,x,minute):
    if b.get('ensemble'):
        parts=[predict(q,x,minute) for q in b['components']]
        return sum(w*q[0] for w,q in zip(b['weights'],parts)),sum(w*q[1] for w,q in zip(b['weights'],parts))
    matrix=b['transform'].transform(x[b['stage_columns']])
    raw=b['estimator'].predict_proba(matrix)[:,1]
    return raw,b['calibrator'].predict(raw,minute)

def voting_weights(families):
    vectors=[]
    for i in range(len(families)):
        for j in range(i+1,len(families)):
            for w in [.25,.5,.75]:
                q=np.zeros(len(families));q[i]=w;q[j]=1-w;vectors.append(q.tolist())
    vectors.append([1/len(families)]*len(families))
    return vectors

def select_voting(bundles,d,x,indices):
    components=[bundles[f] for f in FAMILIES]
    parts=[predict(b,x.loc[indices],d.loc[indices,'snapshot_minute']) for b in components]
    records=[];best=None
    for k,w in enumerate(voting_weights(FAMILIES)):
        p=sum(v*q[1] for v,q in zip(w,parts))
        t,policy,_=select_threshold(d.loc[indices,'label'],p,d.loc[indices,'id'])
        m=metrics(d.loc[indices,'label'],p,t)
        records.append({'candidate_id':f'soft_voting_{k+1:03d}','weights':w,'validation':m,'policy':policy})
        if best is None or rank(m)>rank(best['validation']):
            best={'ensemble':True,'components':components,'weights':w,'threshold':t,'policy':policy,'validation':m,'config':{'candidate_id':f'soft_voting_{k+1:03d}','family':'soft_voting','component_ids':[b['config']['candidate_id'] for b in components]},'model_version':'v6_result_to_draw_20261011','target':'result_to_draw'}
    return best,records
