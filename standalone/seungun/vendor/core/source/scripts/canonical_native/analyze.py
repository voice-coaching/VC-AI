import csv,glob,hashlib,json,math,pathlib,re,statistics,unicodedata
from collections import Counter,defaultdict
import numpy as np
ROOT=pathlib.Path('/workspace/intelligentai/validation/p1_native_demo_refinement_20260928T1310KST')
OLD=pathlib.Path('/workspace/intelligentai/validation/aihub71469_demo_recall_policy_20260928T123725KST')
RAWROOT=pathlib.Path('/workspace/intelligentai/validation/finish_20260916/aihub71469_raw_b0_b1_demo_diagnostic_20260928')
MODEL=pathlib.Path('/workspace/intelligentai/validation/native_korean_phone_eval_20260928T1109KST/native_model/snapshot')
CONSONANTS=set('ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ')
CONS={'B':'ㅂ','BB':'ㅃ','Ph':'ㅍ','D':'ㄷ','DD':'ㄸ','Th':'ㅌ','G':'ㄱ','GG':'ㄲ','Kh':'ㅋ','J':'ㅈ','JJ':'ㅉ','CHh':'ㅊ','S':'ㅅ','SS':'ㅆ','H':'ㅎ','M':'ㅁ','N':'ㄴ','L':'ㄹ','R':'ㄹ','NG':'ㅇ','k':'ㄱ','t':'ㄷ','p':'ㅂ'}
VOW={'A':'ㅏ','EO':'ㅓ','EU':'ㅡ','I':'ㅣ','O':'ㅗ','U':'ㅜ','euI':'ㅢ','iA':'ㅑ','iEO':'ㅕ','iO':'ㅛ','iU':'ㅠ','oA':'ㅘ','uEO':'ㅝ','uI':'ㅟ'}
IPA={'ㄱ':{'ɡ','g','k','ɟ'},'ㄲ':{'k͈'},'ㅋ':{'kʰ'},'ㄴ':{'n','nː','ɲ'},'ㄷ':{'d','t','dʑ'},'ㄸ':{'t͈'},'ㅌ':{'tʰ'},'ㄹ':{'ɭ','ɾ','l'},'ㅁ':{'m'},'ㅂ':{'b','p','bʲ'},'ㅃ':{'p͈'},'ㅍ':{'pʰ'},'ㅅ':{'sʰ','s'},'ㅆ':{'s͈'},'ㅇ':{'ŋ'},'ㅈ':{'dʑ','ʤ'},'ㅉ':{'tɕ͈'},'ㅊ':{'tɕʰ'},'ㅎ':{'h','ɦ'}}
def readcsv(p):return list(csv.DictReader(open(p,newline='')))
def writecsv(p,rows,fields=None):
 assert not p.exists(),p
 with open(p,'x',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
def clean(s):return unicodedata.normalize('NFC',re.sub(r'[.,!?;:。！？]+$','',s.strip()))
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def is_ipa_cons(s):
 return s in set().union(*IPA.values())
def align_cons(ex,ip):
 # Enumerate all highest-scoring monotonic alignments; match +2, skip -1.
 # The target is accepted only when its mapped IPA position is identical in ALL optimal alignments.
 memo={}
 def solve(i,j):
  if (i,j) in memo:return memo[(i,j)]
  if i==len(ex) and j==len(ip):return (0,[()])
  opts=[]
  if i<len(ex):
   v,paths=solve(i+1,j);opts.append((v-1,[((i,None),)+p for p in paths]))
  if j<len(ip):
   v,paths=solve(i,j+1);opts.append((v-1,[p for p in paths]))
  if i<len(ex) and j<len(ip) and ip[j]['label'] in IPA.get(ex[i]['expected_phone'],set()):
   v,paths=solve(i+1,j+1);opts.append((v+2,[((i,j),)+p for p in paths]))
  best=max(v for v,_ in opts);paths=[]
  for v,pp in opts:
   if v==best:paths+=pp
  # Small word-level sequences, but cap paths to fail closed on ambiguity.
  if len(paths)>100000:raise RuntimeError('alignment path explosion')
  memo[(i,j)]=(best,paths)
  return memo[(i,j)]
 return solve(0,0)
def test():
 ex=[{'expected_phone':'ㅌ'},{'expected_phone':'ㄹ'},{'expected_phone':'ㅂ'},{'expected_phone':'ㅌ'}]
 ip=[{'label':x} for x in ['tʰ','ɭ','b','tʰ']]
 _,ps=align_cons(ex,ip);assert {dict(p).get(0) for p in ps}=={0} and {dict(p).get(3) for p in ps}=={3}
 _,ps=align_cons([{'expected_phone':'ㄴ'},{'expected_phone':'ㄴ'}],[{'label':'nː'}])
 assert len({dict(p).get(0) for p in ps})>1
 print('SYNTHETIC_MONOTONIC_INSERTION_DELETION_REPEAT_PASS')
def main():
 test()
 support=[x for x in readcsv(OLD/'contract/target_support_manifest.csv') if x['cohort']=='USER_PAIRED_DIAGNOSTIC']
 assert len(support)==33
 raw=readcsv(RAWROOT/'raw_product_b0_b1_phone_predictions.csv')
 oldp=readcsv(OLD/'predictions/policy_p1_native_only.csv')
 oldkey={(x['recording_id'],int(x['expected_index'])):x for x in oldp}
 rawby=defaultdict(list)
 for x in raw:rawby[x['recording_id']].append(x)
 native={}
 for line in open(OLD/'predictions/native_consonant_predictions.jsonl'):
  x=json.loads(line);native[x['recording_id']]=x
 assert len(native)==26
 vr=json.load(open(MODEL/'vocab.json'));vr.update(json.load(open(MODEL/'added_tokens.json')))
 toks={v:k for k,v in vr.items()}
 mfa={}
 for rid in [f'user_script{k}_{c}' for k in (1,2,3) for c in ('normal','error')]:
  paths=glob.glob(str(RAWROOT/'source_snapshot/task_outputs'/rid/'*'/'result.json'));assert len(paths)==1
  mfa[rid]=(paths[0],json.load(open(paths[0])))
 interval={};loc_status={};loc_evidence={};repair=[]
 for rid,(path,j) in mfa.items():
  rows=rawby[rid];group=defaultdict(list)
  for x in rows:
   if x['original_word_index']:group[int(x['original_word_index'])].append(x)
  words=[w for w in j['word_intervals'] if w['label']]
  for wi,g in group.items():
   g=sorted(g,key=lambda z:int(z['expected_index']))
   if wi>=len(words):continue
   w=words[wi];ph=[p for p in j['phone_intervals'] if p['label'] and p['start_s']>=w['start_s'] and p['end_s']<=w['end_s']]
   if clean(w['label'])!=clean(g[0]['original_word_text']):continue
   if len(g)==len(ph):
    for x,p in zip(g,ph):
     key=(rid,int(x['expected_index']));interval[key]=(p['start_s'],p['end_s']);loc_status[key]='EXACT_COUNT_WORD_INDEX';loc_evidence[key]=p['label']
   else:
    ec=[x for x in g if x['expected_phone'] in CONSONANTS]
    ic=[p for p in ph if is_ipa_cons(p['label'])]
    score,paths=align_cons(ec,ic)
    for ei,x in enumerate(ec):
     choices={dict(p).get(ei) for p in paths}
     key=(rid,int(x['expected_index']))
     if len(choices)==1 and None not in choices:
      ii=next(iter(choices));p=ic[ii]
      interval[key]=(p['start_s'],p['end_s']);loc_status[key]='UNIQUE_MONOTONIC_CONSONANT_MATCH';loc_evidence[key]=p['label']
     else:loc_status[key]='UNRESOLVED_MONOTONIC_AMBIGUITY';loc_evidence[key]=''
 # Validate frozen exact-count locations unchanged.
 for key,old in oldkey.items():
  if key[0] not in mfa:continue
  if old['mfa_start_s']!='':
   assert key in interval and abs(interval[key][0]-float(old['mfa_start_s']))<1e-9 and abs(interval[key][1]-float(old['mfa_end_s']))<1e-9
 for t in support:
  old=t['mapping_status'];rid='user_script'+t['pair_id']+'_error';key=(rid,int(t['expected_index']))
  nkey=(rid.replace('_error','_normal'),key[1]);new='RECOVERED' if key in interval and nkey in interval else 'UNRESOLVED'
  if old=='AMBIGUOUS':
   repair.append({'target_id':t['target_id'],'old_status':old,'new_status':new,'repair_method':'MAX_SCORE_MONOTONIC_CONSONANT_IPA_ALIGNMENT_V1','mapping_unique':int(new=='RECOVERED'),'target_interval_start':interval.get(key,('',''))[0],'target_interval_end':interval.get(key,('',''))[1],'evidence':f"error={loc_evidence.get(key,'')};normal={loc_evidence.get(nkey,'')};source=MFA+expected_phone_role;prediction_blind=YES",'reason_if_unresolved':'' if new=='RECOVERED' else 'MULTIPLE_OPTIMAL_MONOTONIC_MATCHES_OR_NO_MATCH'})
 assert len(repair)==6
 writecsv(ROOT/'audit/localization_repair_audit.csv',repair)
 # For each mapped slot, reproduce the frozen top-1 evidence exactly where old had an interval.
 def evidence(x,key):
  if x['expected_phone'] not in CONSONANTS:return ('NOT_IN_CONSONANT_SCOPE','','','',None)
  if key not in interval:return ('NATIVE_UNRESOLVED','','','LOCATION_UNAVAILABLE',None)
  a,b=interval[key];rid=key[0];runs=[r for r in native[rid]['ctc_runs'] if r['is_phone'] and a<=r['midpoint_s']<b]
  if len(runs)==1 and runs[0]['token'] in CONS:
   run=runs[0];phone=CONS[run['token']]
   if run['token'] in {'k','p','t','NG'} and x['role']!='coda':return ('NATIVE_UNRESOLVED',run['token'],phone,'CODA_TOKEN_ROLE_CONFLICT',run)
   if run['token'] in {'G','GG','Kh','B','BB','Ph','D','DD','Th','J','JJ','CHh','S','SS','H','R'} and x['role']=='coda':return ('NATIVE_UNRESOLVED',run['token'],phone,'ONSET_TOKEN_ROLE_CONFLICT',run)
   return ('NATIVE_MATCH_EXPECTED' if phone==x['expected_phone'] else 'NATIVE_NONCANONICAL_CONSONANT',run['token'],phone,'',run)
  # Reuse deletion anchor logic only if immediate expected neighbors have validated intervals.
  rows=sorted(rawby[rid],key=lambda z:int(z['expected_index']))
  ix=key[1];idx=next(i for i,z in enumerate(rows) if int(z['expected_index'])==ix)
  if not runs and idx>0 and idx+1<len(rows) and rows[idx-1]['original_word_index']==x['original_word_index']==rows[idx+1]['original_word_index']:
   def anchored(z):
    k=(rid,int(z['expected_index']))
    if k not in interval:return False
    s,e=interval[k];rr=[r for r in native[rid]['ctc_runs'] if r['is_phone'] and s<=r['midpoint_s']<e]
    if len(rr)!=1:return False
    tok=rr[0]['token']
    return CONS.get(tok)==z['expected_phone'] or VOW.get(tok)==z['expected_phone']
   if anchored(rows[idx-1]) and anchored(rows[idx+1]):return ('NATIVE_DELETION_EVIDENCE','','','BOTH_ADJACENT_ANCHORS_MATCH',None)
  return ('NATIVE_UNRESOLVED','','',f'PHONE_RUNS_IN_INTERVAL_{len(runs)}',runs[0] if len(runs)==1 else None)
 def score(key,expected,run):
  rid=key[0];arr=np.load(ROOT/'logits'/(rid+'_cpu_fp32.npy'),mmap_mode='r')
  expids=[vr[t] for t,p in CONS.items() if p==expected and t in vr]
  if key not in interval or not expids:return {'expected_score':'','competitor_score':'','competitor_phone':'','local_margin':'','entropy':'','run_margin':''}
  a,b=interval[key];fr=np.arange(len(arr));times=(fr*320+199.5)/16000;ids=np.where((times>=a)&(times<b))[0]
  if len(ids)==0:return {'expected_score':'','competitor_score':'','competitor_phone':'','local_margin':'','entropy':'','run_margin':''}
  v=arr[ids];compet=[(tok,i) for tok,i in vr.items() if tok in CONS and CONS[tok]!=expected]
  e=np.max(v[:,expids],axis=1);cvals=np.stack([v[:,i] for _,i in compet],axis=1);best=np.max(cvals,axis=1);best_at=int(np.argmax(best-e));ctok=compet[int(np.argmax(cvals[best_at]))][0]
  z=v-v.max(axis=1,keepdims=True);prob=np.exp(z);prob/=prob.sum(axis=1,keepdims=True);entropy=float(np.mean(-np.sum(prob*np.log(np.maximum(prob,1e-30)),axis=1)))
  rm=''
  if run is not None and run['token'] in vr and run['token'] in CONS:
   rv=arr[run['frame_start']:run['frame_end_exclusive']]
   rm=float(np.mean(rv[:,vr[run['token']]]-np.max(rv[:,expids],axis=1)))
  return {'expected_score':float(np.max(e)),'competitor_score':float(np.max(best)),'competitor_phone':CONS[ctok],'local_margin':float(np.max(best-e)),'entropy':entropy,'run_margin':rm}
 # Emit all user phone positions for reproducible sweep.
 positions=[]
 for rid in mfa:
  for x in rawby[rid]:
   key=(rid,int(x['expected_index']));ev,tok,ph,why,run=evidence(x,key);s=score(key,x['expected_phone'],run) if x['expected_phone'] in CONSONANTS else {'expected_score':'','competitor_score':'','competitor_phone':'','local_margin':'','entropy':'','run_margin':''}
   old=oldkey[key]
   if old['mfa_start_s']!='' and old['native_evidence']!=ev: print('PARITY_MISMATCH',key,old['native_evidence'],ev,why); raise AssertionError('prior evidence mismatch')
   positions.append({'recording_id':rid,'pair_id':x['pair_id'],'condition':x['condition'],'word_index':x['original_word_index'],'word_text':x['original_word_text'],'expected_index':x['expected_index'],'expected_phone':x['expected_phone'],'role':x['role'],'location_status':loc_status.get(key,'UNRESOLVED'),'mfa_ipa':loc_evidence.get(key,''),'mfa_start_s':interval.get(key,('',''))[0],'mfa_end_s':interval.get(key,('',''))[1],'native_evidence':ev,'native_token':tok,'native_phone':ph,'native_reason':why,'old_p1_warn':old['warn'],**s})
 writecsv(ROOT/'metrics/native_evidence_separation.csv',positions)
 poskey={(x['recording_id'],int(x['expected_index'])):x for x in positions}
 targets=[]
 for t in support:
  rid='user_script'+t['pair_id']+'_error';key=(rid,int(t['expected_index']));p=poskey[key]
  old=oldkey[key];hit=int(old['warn']);newhit=int(p['native_evidence'] in ('NATIVE_NONCANONICAL_CONSONANT','NATIVE_DELETION_EVIDENCE'))
  if hit:cat='HIT_INTENDED' if p['native_phone']==t['intended_phone'] else 'NATIVE_OTHER_PHONE'
  elif key not in interval:cat='LOCALIZATION_UNRESOLVED'
  elif t['target_kind']=='DELETION':cat='DELETION_AMBIGUOUS'
  elif p['native_evidence']=='NATIVE_MATCH_EXPECTED':cat='NATIVE_CANONICALIZED'
  elif p['native_evidence']=='NATIVE_NONCANONICAL_CONSONANT':cat='EVIDENCE_PRESENT_BUT_POLICY_SUPPRESSED'
  elif p['native_reason'].startswith('PHONE_RUNS_IN_INTERVAL_'):cat='GENERAL_WORD_DECODE_FAILURE'
  else:cat='OTHER_EVIDENCE_BASED'
  a,b=interval.get(key,('',''));local=[r['token'] for r in native[rid]['ctc_runs'] if a!='' and r['is_phone'] and a<=r['midpoint_s']<b]
  targets.append({'target_id':t['target_id'],'script_id':t['pair_id'],'recording_id':rid,'word_occurrence_id':t['word_index'],'normal_word':t['target_word'],'intended_error_word':t['intended_error_word'],'operation':t['target_kind'],'expected_phone':t['expected_phone'],'intended_error_phone':t['intended_phone'],'mapping_status':t['mapping_status'],'localization_status':p['location_status'],'current_P1_exact_hit':hit,'post_repair_P1_exact_hit':newhit,'native_top1_phone':p['native_phone'],'native_expected_phone_score':p['expected_score'],'native_intended_phone_score':'','native_best_competitor_phone':p['competitor_phone'],'native_best_competitor_score':p['competitor_score'],'local_score_margin_expected_vs_best':p['local_margin'],'local_score_margin_intended_vs_expected':'','local_entropy_or_available_uncertainty':p['entropy'],'native_local_sequence':'|'.join(local),'expected_local_sequence':t['expected_phone'],'failure_category':cat,'failure_detail':p['native_reason'] or p['location_status'],'run_score_margin':p['run_margin']})
 assert len(targets)==33 and sum(int(x['current_P1_exact_hit']) for x in targets)==15
 writecsv(ROOT/'audit/p1_target_failure_ledger.csv',targets)
 # Confirmed false alert words, exactly prior P1 product-word inventory.
 inv=[x for x in readcsv(OLD/'metrics/full_recording_warning_inventory.csv') if x['policy']=='P1_NATIVE_ONLY' and x['cohort']=='USER_PAIRED_DIAGNOSTIC' and x['condition']=='normal']
 assert len(inv)==18
 fps=[]
 for w in inv:
  rid=w['recording_id'];ixs=[int(z) for z in w['trigger_expected_indices'].split('|')];ps=[poskey[(rid,i)] for i in ixs]
  cats=[]
  for p in ps:
   if p['native_evidence']=='NATIVE_DELETION_EVIDENCE':cats.append('GENERAL_DECODE_FAILURE')
   elif p['location_status']!='EXACT_COUNT_WORD_INDEX':cats.append('LOCALIZATION_SHIFT')
   elif p['role']=='coda' and p['native_token'] in {'k','p','t','NG'}:cats.append('RECOGNIZER_CONFUSION')
   else:cats.append('RECOGNIZER_CONFUSION')
  fps.append({'recording_id':rid,'script_id':rid.split('_')[1],'word_occurrence_id':w['word_index'],'word_text':w['word_text'],'expected_phone_or_phones':'|'.join(p['expected_phone'] for p in ps),'native_observed_phone_or_phones':'|'.join(p['native_phone'] for p in ps),'onset_or_coda_role':'|'.join(p['role'] for p in ps),'left_phone':'','right_phone':'','raw_native_top1':'|'.join(p['native_token'] for p in ps),'expected_score':'|'.join(str(p['expected_score']) for p in ps),'competitor_score':'|'.join(str(p['competitor_score']) for p in ps),'score_margin':'|'.join(str(p['run_margin']) for p in ps),'entropy_or_available_uncertainty':'|'.join(str(p['entropy']) for p in ps),'localization_status':'|'.join(p['location_status'] for p in ps),'word_alignment_status':'MFA_WORD_MATCH','phonological_context_notes':'MFA-aligned native CTC top-1 disagreement; user confirmed normal recording','false_alert_failure_category':cats[0]})
 writecsv(ROOT/'audit/p1_normal_false_alert_ledger.csv',fps)
 summary={'target_failure_categories':dict(Counter(x['failure_category'] for x in targets)),'normal_false_alert_categories':dict(Counter(x['false_alert_failure_category'] for x in fps)),'recovered':sum(x['new_status']=='RECOVERED' for x in repair),'old_hits':15,'post_repair_hits':sum(int(x['post_repair_P1_exact_hit']) for x in targets),'position_rows':len(positions),'cpu_logits_replay_exact':6,'score_semantics':'Raw CTC logits; local margin = max same-frame competing consonant logit minus best expected consonant logit; run margin = mean observed-run logit minus best expected logit; entropy = softmax distribution entropy over all 45 symbols in localized frames.'}
 (ROOT/'metrics/native_evidence_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
