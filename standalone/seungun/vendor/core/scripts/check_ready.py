"""Read an active release run's sidecar readiness receipt."""
from __future__ import annotations
import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument("run_output");a=p.parse_args()
root=Path(a.run_output)
ready=json.loads((root/"host_ready.json").read_text())
assert ready["status"]=="READY" and ready["startup"]["ready"]["direct_candidate"]
pid=int(ready["host_pid"])
os.kill(pid,0)
print(json.dumps({"ready":True,"host_pid":pid,"worker_pid":ready["worker_pid"],
 "direct_kalpy":True},ensure_ascii=False))
