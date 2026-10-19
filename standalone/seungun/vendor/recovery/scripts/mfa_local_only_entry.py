"""Experimental MFA launcher. Installed MFA/Hugging Face files are never edited."""
import hashlib, json, os, runpy, sys
from pathlib import Path

RECOVERY=Path("/workspace/intelligentai/validation/finish_20260916/latency_optimization_20260921T044013_2c3be9/mfa_startup_recovery_20260921T065201_ac461c")
VALIDATION=RECOVERY.parent.parent
MFA_EXE=VALIDATION/"envs/mfa341/bin/mfa"
APPROVED_ROOT=RECOVERY.parent/"bench_20260921T061213_21865d/results/baseline_cpu/request_00/mfa"
REPOSITORY="MontrealCorpusTools/korean_mfa"
REVISION="0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd"

def verify_manifest(root):
    root=Path(root)
    manifest_path=root/"manifests/korean_mfa.json"
    data=json.loads(manifest_path.read_text())
    if (data.get("schema_version"),data.get("repository"),data.get("revision"),data.get("license")) != ("1.0.0",REPOSITORY,REVISION,"CC-BY-4.0"):
        raise ValueError("manifest identity mismatch")
    cache=(root/"huggingface").resolve(strict=True)
    snapshot=(cache/"models--MontrealCorpusTools--korean_mfa"/"snapshots"/REVISION).resolve(strict=True)
    if not snapshot.is_relative_to(cache):
        raise ValueError("snapshot escapes approved cache")
    entries=data.get("files")
    if not isinstance(entries,list) or not entries:
        raise ValueError("empty manifest")
    for entry in entries:
        relative=Path(entry["path"])
        expected=entry["sha256"]
        if relative.is_absolute() or ".." in relative.parts or not isinstance(expected,str) or len(expected)!=64:
            raise ValueError("invalid manifest entry")
        p=(snapshot/relative).resolve(strict=True)
        if not p.is_relative_to(snapshot) or not p.is_file():
            raise ValueError("invalid model file")
        h=hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
        if h.hexdigest()!=expected:
            raise ValueError("model manifest hash mismatch: "+str(relative))
    return cache,snapshot

def cached_resolver(download, *, root=APPROVED_ROOT):
    def resolve(*,repo_id,revision,**kwargs):
        if os.environ.get("HF_HUB_OFFLINE")!="1" or os.environ.get("RECOVERY_GUARD_ACTIVE")!="1":
            raise RuntimeError("offline isolation is required")
        if repo_id!=REPOSITORY or revision!=REVISION or kwargs:
            raise ValueError("unsupported model request")
        cache,snapshot=verify_manifest(root)
        result=download(repo_id=repo_id,revision=revision,cache_dir=str(cache),local_files_only=True)
        if Path(result).resolve(strict=True)!=snapshot:
            raise ValueError("unexpected snapshot path")
        return str(snapshot)
    return resolve

def main():
    args=sys.argv[1:]
    if not args or args[0] not in ("version","align_hf"):
        raise ValueError("only existing version/align_hf commands are allowed")
    if args[0]=="version" and len(args)!=1:
        raise ValueError("unexpected version arguments")
    if args[0]=="align_hf":
        if len(args)!=11 or args[1]!="--temporary_directory" or args[3:8]!=["--single_speaker","--no_tokenization","--use_g2p","--num_jobs","1"] or args[9]!=REPOSITORY+"@"+REVISION:
            raise ValueError("alignment contract changed")
        for value in (args[2],args[8],args[10]):
            if not Path(value).resolve().is_relative_to(RECOVERY):
                raise ValueError("request path is outside recovery")
    if os.environ.get("HF_HUB_OFFLINE")!="1" or os.environ.get("RECOVERY_GUARD_ACTIVE")!="1":
        raise RuntimeError("offline isolation is required")
    import huggingface_hub
    huggingface_hub.snapshot_download=cached_resolver(huggingface_hub.snapshot_download)
    sys.argv=[str(MFA_EXE),*args]
    runpy.run_path(str(MFA_EXE),run_name="__main__")

if __name__=="__main__":
    main()
