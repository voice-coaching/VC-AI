import argparse,csv,hashlib,json,os,pathlib,resource,time,wave
import numpy as np
import torch
from transformers import Wav2Vec2FeatureExtractor,Wav2Vec2ForCTC
ROOT=pathlib.Path("/workspace/intelligentai/validation/aihub71469_demo_recall_policy_20260928T123725KST")
MODEL=pathlib.Path("/workspace/intelligentai/validation/native_korean_phone_eval_20260928T1109KST/native_model/snapshot")
AUDIO=pathlib.Path("/workspace/intelligentai/validation/native_korean_phone_eval_20260928T1109KST/evaluation/audio_manifest.csv")
CONTRACT=ROOT/"contract/demo_recall_policy_contract_v1.json"
WEIGHTS_SHA="b2ec663558606d651a718fa1eaa20c6d504df173725ecbc66df74c22267e2d68"
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(4*1024*1024),b""):h.update(b)
 return h.hexdigest()
def collapse(ids,vocab,frames):
 out=[];prev=None;start=0
 for i,t in enumerate(ids+[None]):
  if t==prev:continue
  if prev is not None and prev!=42:
   tok=vocab[str(prev)]
   mid=((start+i-1)/2*320+199.5)/16000
   out.append({"token_id":prev,"token":tok,"frame_start":start,"frame_end_exclusive":i,"midpoint_s":mid,
               "is_phone":prev<=39})
  prev=t;start=i
 return out
def test():
 vocab={str(i):str(i) for i in range(45)}
 assert [x["token_id"] for x in collapse([42,1,1,42,1,42,40,41,43,44,42],vocab,11)]==[1,1,40,41,43,44]
 assert abs(collapse([1],vocab,1)[0]["midpoint_s"]-199.5/16000)<1e-12
 assert collapse([42,42],vocab,2)==[]
 print(json.dumps({"synthetic_ctc_blank_repeat_special_unknown":"PASS"}))
def readwav(p):
 with wave.open(str(p),"rb") as w:
  assert (w.getnchannels(),w.getframerate(),w.getsampwidth(),w.getcomptype())==(1,16000,2,"NONE")
  n=w.getnframes();data=w.readframes(n)
 return np.frombuffer(data,dtype="<i2").astype(np.float32)/32768.0,n
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--test",action="store_true");ap.add_argument("--ids",nargs="*");ap.add_argument("--all",action="store_true");args=ap.parse_args()
 if args.test:return test()
 assert args.all or args.ids
 assert sha(MODEL/"pytorch_model.bin")==WEIGHTS_SHA
 os.environ["HF_HUB_OFFLINE"]="1";os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
 torch.set_num_threads(8)
 vocabraw=json.load(open(MODEL/"vocab.json"));vocabraw.update(json.load(open(MODEL/"added_tokens.json")))
 assert sorted(vocabraw.values())==list(range(45))
 vocab={str(v):k for k,v in vocabraw.items()}
 rows=list(csv.DictReader(open(AUDIO)))
 choose=rows if args.all else [x for x in rows if x["recording_id"] in set(args.ids)]
 assert len(choose)==(26 if args.all else len(set(args.ids)))
 load0=time.monotonic()
 fe=Wav2Vec2FeatureExtractor.from_pretrained(str(MODEL),local_files_only=True)
 model=Wav2Vec2ForCTC.from_pretrained(str(MODEL),local_files_only=True).to("cpu").float().eval()
 load_seconds=time.monotonic()-load0
 assert model.lm_head.out_features==45 and model.config.pad_token_id==42
 work=ROOT/"predictions/work_units";work.mkdir(exist_ok=True)
 cs=sha(CONTRACT);done=0
 for row in choose:
  rid=row["recording_id"];wav=pathlib.Path(row["prepared_wav_path"])
  assert sha(wav)==row["prepared_wav_sha256"]
  target=work/(rid+".json")
  if target.exists():
   old=json.load(open(target))
   assert old["recording_id"]==rid and old["audio_sha256"]==row["prepared_wav_sha256"] and old["contract_sha256"]==cs and old["model_weights_sha256"]==WEIGHTS_SHA
   print(json.dumps({"recording_id":rid,"status":"REUSED_VERIFIED","seconds":old["inference_seconds"]}),flush=True);done+=1;continue
  x,n=readwav(wav)
  inp=fe(x,sampling_rate=16000,return_tensors="pt")
  t0=time.monotonic()
  with torch.inference_mode():
   logits=model(inp.input_values.to("cpu")).logits[0]
   ids=logits.argmax(dim=-1).tolist()
  elapsed=time.monotonic()-t0
  result={"recording_id":rid,"cohort":row["cohort"],"pair_id":row["pair_id"],"role":row["role"],
   "audio_sha256":row["prepared_wav_sha256"],"contract_sha256":cs,"model_weights_sha256":WEIGHTS_SHA,
   "native_revision":"e26ff9dfb62169acf445d0060ef56863c018b20e",
   "sample_count":n,"sample_rate":16000,"output_frames":len(ids),"frame_argmax_ids":ids,
   "ctc_runs":collapse(ids,vocab,len(ids)),"inference_seconds":elapsed,"model_load_seconds_first_process":load_seconds,
   "peak_cpu_rss_kib_process":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"device":"cpu","dtype":"float32","status":"PASS"}
  tmp=target.with_suffix(".json.inprogress")
  with open(tmp,"x") as f:json.dump(result,f,ensure_ascii=False,separators=(",",":"));f.write("\n")
  os.replace(tmp,target)
  print(json.dumps({"recording_id":rid,"status":"PASS","frames":len(ids),"ctc_runs":len(result["ctc_runs"]),"seconds":elapsed}),flush=True)
  done+=1
 if args.all:
  out=ROOT/"predictions/native_consonant_predictions.jsonl"
  if out.exists():raise RuntimeError("aggregate exists; do not overwrite")
  with open(out,"x") as f:
   for row in rows:
    with open(work/(row["recording_id"]+".json")) as g:f.write(g.read())
  assert sum(1 for _ in open(out))==26
  print(json.dumps({"aggregate":"PASS","recordings":26,"path":str(out)}),flush=True)
if __name__=="__main__":main()
