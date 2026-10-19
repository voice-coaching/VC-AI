from __future__ import annotations
import argparse,csv,hashlib,json,math,os,pathlib,sys,unicodedata
from collections import Counter,defaultdict
import numpy as np
from analyze import align_cons,is_ipa_cons,clean,CONS,IPA,CONSONANTS
ROOT=pathlib.Path('/workspace/intelligentai/validation/refined_p1_new_recording_smoke_20260928T143000KST')
OLD=pathlib.Path('/workspace/intelligentai/validation/p1_native_demo_refinement_20260928T1310KST')
MODEL=pathlib.Path('/workspace/intelligentai/validation/native_korean_phone_eval_20260928T1109KST/native_model/snapshot')
THETA=None  # Set from SHA-verified frozen policy JSON by the integration runner.
POLICY_SHA='c3c4a11927b748c4998d3cd0a5a578f9e77faaf4863780be0984edaa95a11d50'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def rc(p):return list(csv.DictReader(open(p,newline='',encoding='utf-8')))
def wc(p,rows,fields=None):
 assert not p.exists()
 with open(p,'x',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields or list(rows[0]));w.writeheader();w.writerows(rows)
def test():
 assert sha(OLD/'contract/refined_p1_policy_contract.json')==POLICY_SHA
 ex=[{'expected_phone':'ㄲ'},{'expected_phone':'ㅅ'}];ip=[{'label':'k͈'},{'label':'s'}]
 _,paths=align_cons(ex,ip);assert {dict(p).get(0) for p in paths}=={0}
 assert clean('칼날이,')=='칼날이'
 assert ('쪽으로' in '반대쪽으로')
 print('SYNTHETIC_LOCATION_POLICY_TEST_PASS',flush=True)
def target_expected_indices(info,targets):
 out={}
 groups=defaultdict(list)
 for x in info['expected']:groups[int(x['original_word_index'])].append(x)
 for t in targets:
  w=int(t['word_token_index']);full=clean(t['normal_full_token']);small=t['normal_word'];err=t['error_word']
  assert len(small)==len(err)
  diffs=[i for i,(a,b) in enumerate(zip(small,err)) if a!=b]
  assert len(diffs)==1,(t['target_id'],diffs)
  start=full.find(small)
  assert start>=0 and full.count(small)==1,(t['target_id'],full)
  ordinal=start+diffs[0]
  gs=groups[w];by_syl=defaultdict(list)
  for x in gs:by_syl[int(x['syllable_index'])].append(x)
  syls=sorted(by_syl)
  chosen=[]
  if len(syls)==len(full) and ordinal<len(syls):
   chosen=[x for x in by_syl[syls[ordinal]] if x['expected_phone']==t['expected_phone_instruction'] and x['role']=='onset']
  out[t['target_id']]={'expected_index':int(chosen[0]['expected_index']) if len(chosen)==1 else None,'target_syllable_ordinal':ordinal,'mapping_status':'UNIQUE_EXPECTED_POSITION' if len(chosen)==1 else 'EXPECTED_PHONE_POSITION_UNRESOLVED','g2p_syllable_count':len(syls),'original_syllable_count':len(full)}
 return out
def get_locations(info):
 expected=info['expected'];allwords=[x for x in info['word_intervals'] if x['label']]
 byword=defaultdict(list)
 for x in expected:byword[int(x['original_word_index'])].append(x)
 loc={};status={};wordmap={}
 for wi,g in sorted(byword.items()):
  g=sorted(g,key=lambda z:int(z['expected_index']))
  if wi>=len(allwords):
   for x in g:status[int(x['expected_index'])]='WORD_INDEX_UNRESOLVED'
   continue
  w=allwords[wi];wordmap[wi]=w
  if clean(w['label'])!=clean(g[0]['original_word_text']):
   for x in g:status[int(x['expected_index'])]='WORD_TEXT_UNRESOLVED'
   continue
  ph=[p for p in info['phone_intervals'] if p['label'] and p['start_s']>=w['start_s'] and p['end_s']<=w['end_s']]
  if len(g)==len(ph):
   for x,p in zip(g,ph):
    k=int(x['expected_index']);loc[k]=p;status[k]='EXACT_COUNT_WORD_INDEX'
  else:
   ec=[x for x in g if x['expected_phone'] in CONSONANTS];ic=[p for p in ph if is_ipa_cons(p['label'])]
   _,paths=align_cons(ec,ic)
   for ei,x in enumerate(ec):
    k=int(x['expected_index']);choices={dict(p).get(ei) for p in paths}
    if len(choices)==1 and None not in choices:
     p=ic[next(iter(choices))];loc[k]=p;status[k]='UNIQUE_MONOTONIC_CONSONANT_MATCH'
    else:status[k]='UNRESOLVED_MONOTONIC_AMBIGUITY'
 return loc,status,wordmap
def evaluate_position(x,p,runs,arr,vr):
 expected=x['expected_phone'];role=x['role']
 if expected not in CONSONANTS:return {'policy_warning':0,'decision_reason':'OUTSIDE_CONSONANT_SCOPE'}
 if p is None:return {'policy_warning':0,'decision_reason':'LOCATION_UNRESOLVED'}
 a=float(p['start_s']);b=float(p['end_s'])
 found=[z for z in runs if z['is_phone'] and z['token'] in CONS and a<=z['midpoint_s']<b]
 if len(found)!=1:return {'policy_warning':0,'decision_reason':'NO_UNIQUE_CONSONANT_RUN','consonant_run_count':len(found)}
 z=found[0];tok=z['token'];ph=CONS[tok]
 if tok in {'k','p','t','NG'} and role!='coda':return {'policy_warning':0,'decision_reason':'CODA_TOKEN_ROLE_CONFLICT','native_token':tok,'native_phone':ph,'consonant_run_count':1}
 if tok in {'G','GG','Kh','B','BB','Ph','D','DD','Th','J','JJ','CHh','S','SS','H','R'} and role=='coda':return {'policy_warning':0,'decision_reason':'ONSET_TOKEN_ROLE_CONFLICT','native_token':tok,'native_phone':ph,'consonant_run_count':1}
 expids=[vr[t] for t,v in CONS.items() if v==expected and t in vr]
 assert expids and tok in vr
 rv=arr[int(z['frame_start']):int(z['frame_end_exclusive'])]
 margin=float(np.mean(rv[:,vr[tok]]-np.max(rv[:,expids],axis=1)))
 warning=int(ph!=expected and margin>=THETA)
 return {'policy_warning':warning,'decision_reason':'WARN' if warning else ('CANONICAL_EXPECTED' if ph==expected else 'BELOW_FROZEN_MARGIN'),'native_token':tok,'native_phone':ph,'consonant_run_count':1,'run_frame_start':z['frame_start'],'run_frame_end_exclusive':z['frame_end_exclusive'],'margin':margin}
def score_local(p,arr,expected,vr):
 if p is None or expected not in CONSONANTS:return {}
 a=float(p['start_s']);b=float(p['end_s'])
 times=(np.arange(len(arr))*320+199.5)/16000
 ids=np.where((times>=a)&(times<b))[0]
 if len(ids)==0:return {}
 v=arr[ids];expids=[vr[t] for t,q in CONS.items() if q==expected and t in vr]
 e=np.max(v[:,expids],axis=1)
 comp=[(t,i) for t,i in vr.items() if t in CONS and CONS[t]!=expected]
 c=np.stack([v[:,i] for _,i in comp],axis=1)
 at=int(np.argmax(np.max(c,axis=1)-e));ci=int(np.argmax(c[at]))
 return {'expected_logit':float(np.max(e)),'best_competitor_phone':CONS[comp[ci][0]],'best_competitor_token':comp[ci][0],'best_competitor_logit':float(np.max(c)),'local_margin':float(np.max(np.max(c,axis=1)-e))}
def main():
 a=argparse.ArgumentParser();a.add_argument('--test',action='store_true');a.add_argument('--ids',nargs='*');z=a.parse_args()
 test()
 if z.test:return
 assert z.ids
 assert len(z.ids)==len(set(z.ids))
 assert sha(OLD/'contract/refined_p1_policy_contract.json')==POLICY_SHA
 vr=json.load(open(MODEL/'vocab.json'));vr.update(json.load(open(MODEL/'added_tokens.json')))
 targets=rc(ROOT/'contract/script_target_manifest.csv')
 allpositions=[];alltargets=[];normalwarnings=[];offwarnings=[];allnative=[]
 for rid in z.ids:
  info=json.load(open(ROOT/'inputs'/(rid+'_alignment.json')))
  native=json.load(open(ROOT/'predictions/work_units'/(rid+'.json')))
  assert native['device']=='cpu' and native['dtype']=='float32' and native['policy_sha256']==POLICY_SHA and native['audio_sha256']==info['prepared_wav_sha256']
  arr=np.load(native['logits_path'],mmap_mode='r');assert sha(native['logits_path'])==native['logits_sha256']
  loc,status,wordmap=get_locations(info)
  mytargets=[t for t in targets if t['script_id']==rid[0]]
  tix=target_expected_indices(info,mytargets)
  for t in mytargets:
   if tix[t['target_id']]['expected_index'] is None:print('TARGET_MAPPING_UNRESOLVED',rid,t['target_id'],flush=True)
  target_indices={v['expected_index']:tid for tid,v in tix.items() if v['expected_index'] is not None}
  by_index={}
  for x in info['expected']:
   if x['expected_phone'] not in CONSONANTS:continue
   ix=int(x['expected_index']);wi=int(x['original_word_index']);p=loc.get(ix)
   decision=evaluate_position(x,p,native['ctc_runs'],arr,vr);scores=score_local(p,arr,x['expected_phone'],vr)
   word=wordmap.get(wi,{})
   row={'recording_id':rid,'script_id':rid[0],'condition':rid.split('_')[1],'expected_index':ix,'original_word_index':wi,'word_text':x['original_word_text'],'expected_phone':x['expected_phone'],'role':x['role'],'g2p_syllable_text':x['syllable_text'],'g2p_syllable_index':x['syllable_index'],'localization_status':status.get(ix,'UNRESOLVED'),'mfa_ipa':p['label'] if p else '','mfa_start_s':p['start_s'] if p else '','mfa_end_s':p['end_s'] if p else '','word_start_s':word.get('start_s',''),'word_end_s':word.get('end_s',''),'target_id':target_indices.get(ix,''),**decision,**scores}
   allpositions.append(row);by_index[ix]=row
  for t in mytargets:
   ti=tix[t['target_id']];ix=ti['expected_index'];p=by_index.get(ix) if ix is not None else None
   if p is None:rtype='UNRESOLVED';hit=0;direction=0
   elif p['localization_status'] in {'WORD_INDEX_UNRESOLVED','WORD_TEXT_UNRESOLVED','UNRESOLVED_MONOTONIC_AMBIGUITY','UNRESOLVED'}:rtype='UNRESOLVED';hit=0;direction=0
   elif p['decision_reason']=='NO_UNIQUE_CONSONANT_RUN':rtype='NO_LOCAL_RUN';hit=0;direction=0
   elif p.get('native_phone','')==t['expected_phone_instruction']:rtype='CANONICAL_EXPECTED';hit=0;direction=0
   elif int(p['policy_warning']):
    hit=1;direction=int(p.get('native_phone','')==t['intended_phone_instruction']);rtype='EXACT_INTENDED_CONSONANT' if direction else 'NONCANONICAL_OTHER_CONSONANT'
   else:hit=0;direction=0;rtype='UNRESOLVED'
   alltargets.append({'target_id':t['target_id'],'script_id':rid[0],'recording_id':rid,'normal_word':t['normal_word'],'error_word':t['error_word'],'expected_phone':t['expected_phone_instruction'],'intended_phone':t['intended_phone_instruction'],'expected_index':ix if ix is not None else '','word_index':t['word_token_index'],'target_syllable_ordinal':ti['target_syllable_ordinal'],'mapping_status':ti['mapping_status'],'localization_status':p['localization_status'] if p else 'UNRESOLVED','native_observed_phone':p.get('native_phone','') if p else '','expected_logit':p.get('expected_logit','') if p else '','best_competitor_phone':p.get('best_competitor_phone','') if p else '','best_competitor_logit':p.get('best_competitor_logit','') if p else '','margin':p.get('margin','') if p else '','refined_P1_reaction':hit,'correct_error_direction':direction,'reaction_type':rtype,'decision_reason':p.get('decision_reason','') if p else 'POSITION_UNRESOLVED'})
  for p in by_index.values():
   if not int(p['policy_warning']):continue
   if rid.endswith('normal'):
    normalwarnings.append({'recording_id':rid,'script_id':rid[0],'word_index':p['original_word_index'],'word_text':p['word_text'],'expected_index':p['expected_index'],'expected_phone':p['expected_phone'],'native_phone':p.get('native_phone',''),'margin':p.get('margin',''),'localization_status':p['localization_status'],'word_start_s':p['word_start_s'],'word_end_s':p['word_end_s'],'label':'NORMAL_REFERENCE_WARNING'})
   elif not p['target_id']:
    offwarnings.append({'recording_id':rid,'script_id':rid[0],'word_index':p['original_word_index'],'word_text':p['word_text'],'expected_index':p['expected_index'],'expected_phone':p['expected_phone'],'native_phone':p.get('native_phone',''),'margin':p.get('margin',''),'localization_status':p['localization_status'],'word_start_s':p['word_start_s'],'word_end_s':p['word_end_s'],'label':'OFF_TARGET_UNKNOWN'})
  allnative.append(native)
  print(json.dumps({'recording_id':rid,'consonant_positions':len(by_index),'warnings':sum(int(x['policy_warning']) for x in by_index.values()),'resolved_targets':sum(tix[t['target_id']]['expected_index'] is not None for t in mytargets)}),flush=True)
 assert len(alltargets)==32
 errors=[t for t in alltargets if t['recording_id'].endswith('error')]
 assert len(errors)==16
 normal_word_keys={(x['recording_id'],x['word_index']) for x in normalwarnings}
 off_word_keys={(x['recording_id'],x['word_index']) for x in offwarnings}
 actual={'A':{'targets':8},'B':{'targets':8}}
 for sid in ('A','B'):
  ts=[x for x in errors if x['script_id']==sid]
  actual[sid].update({'exact_target_reactions':sum(int(x['refined_P1_reaction']) for x in ts),'correct_error_direction':sum(int(x['correct_error_direction']) for x in ts),'normal_words':len(json.load(open(ROOT/'inputs/script_manifest.json'))['normal'][sid].split()),'normal_warning_words':len({k for k in normal_word_keys if k[0].startswith(sid)}),'error_off_target_unknown_words':len({k for k in off_word_keys if k[0].startswith(sid)}),'unresolved_targets':sum(x['reaction_type']=='UNRESOLVED' for x in ts)})
 combined={k:sum(actual[s][k] for s in ('A','B')) for k in actual['A']}
 decision='NEW_SMOKE_STRONG_PASS' if combined['exact_target_reactions']>=10 and combined['normal_warning_words']<=3 and combined['unresolved_targets']==0 else ('NEW_SMOKE_USABLE' if combined['exact_target_reactions']>=8 and combined['normal_warning_words']<=5 else ('NEW_SMOKE_NO_GO' if combined['exact_target_reactions']<8 or combined['normal_warning_words']>5 else 'NEW_SMOKE_MIXED'))
 if actual['A']['exact_target_reactions']>=6 and actual['B']['exact_target_reactions']<=2 or actual['B']['exact_target_reactions']>=6 and actual['A']['exact_target_reactions']<=2:decision='NEW_SMOKE_MIXED'
 wc(ROOT/'predictions/refined_p1_position_results.csv',allpositions,fields=sorted(set().union(*(x.keys() for x in allpositions))))
 wc(ROOT/'presentation_evidence/all_candidate_evidence.csv',allpositions,fields=sorted(set().union(*(x.keys() for x in allpositions))))
 wc(ROOT/'metrics/target_results.csv',errors)
 wc(ROOT/'metrics/normal_warning_results.csv',normalwarnings,fields=['recording_id','script_id','word_index','word_text','expected_index','expected_phone','native_phone','margin','localization_status','word_start_s','word_end_s','label'])
 wc(ROOT/'metrics/off_target_warning_results.csv',offwarnings,fields=['recording_id','script_id','word_index','word_text','expected_index','expected_phone','native_phone','margin','localization_status','word_start_s','word_end_s','label'])
 with open(ROOT/'predictions/native_predictions.jsonl','x') as f:
  for n in allnative:f.write(json.dumps(n,ensure_ascii=False,separators=(',',':'))+'\n')
 metric={'policy_identity':'PASS','new_audio_identity':'PASS','cpu_fp32_execution':'PASS','decision':decision,'by_script':actual,'combined':combined,'frozen_margin_exact':THETA,'frozen_policy_sha256':POLICY_SHA,'normal_label':'USER_RECORDED_NORMAL_REFERENCE','error_label':'INTENDED_ERROR_TARGET','normal_warning_position_count':len(normalwarnings),'normal_warning_word_count':len(normal_word_keys),'off_target_warning_position_count':len(offwarnings),'off_target_warning_word_count':len(off_word_keys),'total_error_warning_word_count':len({(x['recording_id'],x['original_word_index']) for x in allpositions if x['condition']=='error' and int(x['policy_warning'])}),'total_normal_warning_word_count':len(normal_word_keys),'target_count':16,'retuning':False}
 (ROOT/'metrics/smoke_metrics.json').write_text(json.dumps(metric,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(metric,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
