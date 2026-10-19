"""Transport-only host for the unmodified historical MFA JSON sidecar."""
from __future__ import annotations
import argparse,hashlib,json,os,socket,sys,time,traceback,wave
from pathlib import Path
def sha(path):
 h=hashlib.sha256()
 with Path(path).open("rb") as f:
  for b in iter(lambda:f.read(1<<20),b""):h.update(b)
 return h.hexdigest()
def save(path,value):
 Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n")
def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--runtime",required=True)
 ap.add_argument("--socket",required=True)
 ap.add_argument("--receipt",required=True)
 args=ap.parse_args()
 runtime=Path(args.runtime).resolve()
 sys.path.insert(0,str(runtime))
 from demo_rpc import MFAClient
 from demo_common import L,SOURCE
 sys.path.insert(0,str(SOURCE/"src"))
 from korean_pronunciation_poc.textgrid import parse_textgrid
 client=None;server=None;seen=set();requests=[]
 sockpath=Path(args.socket)
 try:
  assert not sockpath.exists()
  client=MFAClient()
  version=client.call("version")
  assert version["version"]=="3.4.1"
  startup=client.call("start")
  ready=startup["ready"]
  assert ready["direct_candidate"] is True and ready["aligner_class"].endswith("DirectAligner")
  assert len(ready["queue_adapter"]["aliases"])==6
  server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
  server.bind(str(sockpath));server.listen(1)
  save(args.receipt,{"status":"READY","host_pid":os.getpid(),"worker_pid":startup["pid"],
      "runtime":str(runtime),"socket":str(sockpath),"version":version,"startup":startup,
      "host_ready_monotonic":time.perf_counter()})
  conn,_=server.accept()
  with conn:
   stream=conn.makefile("rwb",buffering=0)
   while True:
    line=stream.readline()
    if not line:break
    try:
     req=json.loads(line)
     op=req["op"]
     if op=="version":out=version
     elif op=="start":out=startup
     elif op=="resident":
      rid=req["request_id"]
      if not rid or rid in seen:raise ValueError("DUPLICATE_OR_EMPTY_REQUEST_ID")
      seen.add(rid)
      argv=req["argv"]
      assert len(argv)==11 and argv[0]=="align_hf"
      corpus=Path(argv[8]).resolve()
      if not corpus.is_relative_to(L/"tmp/pipeline"):raise ValueError("INPUT_ESCAPE")
      files=list(corpus.iterdir())
      if len(files)!=2 or sorted(x.suffix for x in files)!=[".lab",".wav"] or any(x.is_symlink() for x in files):raise ValueError("INPUT_SHAPE")
      wav=next(x for x in files if x.suffix==".wav")
      lab=wav.with_suffix(".lab")
      canonical=Path(req["canonical_wav_path"]).resolve()
      if sha(canonical)!=req["canonical_wav_sha256"] or sha(wav)!=req["canonical_wav_sha256"]:raise ValueError("CANONICAL_WAV_SHA_MISMATCH")
      if sha(lab)!=req["transcript_sha256"] or lab.read_text()!=req["normalized_transcript"]:raise ValueError("TRANSCRIPT_MISMATCH")
      duration=float(req["audio_duration_s"])
      if not 0 < duration < 60:raise ValueError("INVALID_CANONICAL_AUDIO_DURATION")
      t=time.perf_counter()
      rec=client.call("resident",argv=argv)
      if not rec["worker"]["workflow_executed"]:raise RuntimeError("DIRECT_KALPY_NOT_EXECUTED")
      parsed=parse_textgrid(Path(rec["output"]),audio_duration_s=duration)
      out={**rec,"request_id":rid,"status":"ok",
          "canonical_wav_sha256":req["canonical_wav_sha256"],
          "transcript_sha256":req["transcript_sha256"],
          "word_intervals":[x.model_dump(mode="json") for x in parsed.word_intervals],
          "phone_intervals":[x.model_dump(mode="json") for x in parsed.phone_intervals],
          "alignment_provenance":{"actual_direct_kalpy":True,"worker_request_id":rec["worker"]["request_id"],
              "generation":rec["generation"],"ordinal":rec["ordinal"],
              "textgrid_sha256":sha(rec["output"])},
          "host_request_wall_s":time.perf_counter()-t}
      requests.append({"request_id":rid,"worker_request_id":rec["worker"]["request_id"],
          "generation":rec["generation"],"ordinal":rec["ordinal"],"textgrid_sha256":sha(rec["output"])})
      save(runtime/"host_requests.json",requests)
     elif op=="close":
      out=client.call("close")
     else:raise ValueError("UNKNOWN_OPERATION")
     stream.write(json.dumps({"ok":True,"result":out},ensure_ascii=False,default=str).encode()+b"\n")
     if op=="close":break
    except BaseException:
     err=traceback.format_exc()
     save(runtime/"host_failure.json",{"request":req if "req" in locals() else None,"error":err})
     stream.write(json.dumps({"ok":False,"error":err}).encode()+b"\n")
     break
  save(runtime/"host_exit.json",{"status":"CLOSED","requests":requests,"host_pid":os.getpid()})
  return 0
 except BaseException:
  save(runtime/"host_exit.json",{"status":"FAILED","error":traceback.format_exc(),"requests":requests})
  return 1
 finally:
  if client:
   try:client.close()
   except BaseException:pass
  if server:server.close()
  if sockpath.exists():sockpath.unlink()
if __name__=="__main__":sys.exit(main())
