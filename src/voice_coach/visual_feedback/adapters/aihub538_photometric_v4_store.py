"""Inode-bound durable one-shot ledger for P6 v4."""
from __future__ import annotations
import json, os, secrets, hashlib, stat, subprocess, sys
from pathlib import Path
from ..models.integrity import require_sha256, sha256_document

class Aihub538PhotometricV4IssuanceError(RuntimeError): pass

class Aihub538PhotometricV4IssuanceStore:
    def __init__(self,directory:Path)->None:
        if not isinstance(directory,Path) or not directory.is_absolute(): raise ValueError("p6_v4_issuance_directory_invalid")
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode&0o077: raise ValueError("p6_v4_issuance_directory_permissions_invalid")
        self._directory=directory.resolve(strict=True); self._parent=self._directory.parent
        self._parent_fd=os.open(self._parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW)
        self._dir_fd=os.open(self._directory.name,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=self._parent_fd)
        self._dir_identity=(os.fstat(self._dir_fd).st_dev,os.fstat(self._dir_fd).st_ino)
        self._parent_identity=(os.fstat(self._parent_fd).st_dev,os.fstat(self._parent_fd).st_ino)
        self._reload_launches:dict[str,tuple[object,...]]={}
    def __del__(self)->None:
        for name in ("_dir_fd","_parent_fd"):
            try: os.close(getattr(self,name))
            except (AttributeError,OSError): pass
    def _verify_identity(self)->None:
        ds=os.fstat(self._dir_fd); ps=os.fstat(self._parent_fd)
        try: named=os.stat(self._directory.name,dir_fd=self._parent_fd,follow_symlinks=False)
        except OSError: raise Aihub538PhotometricV4IssuanceError("p6_v4_issuance_directory_replaced") from None
        if (ds.st_dev,ds.st_ino)!=self._dir_identity or (ps.st_dev,ps.st_ino)!=self._parent_identity or (named.st_dev,named.st_ino)!=self._dir_identity or not os.path.samestat(os.stat(self._parent),ps):
            raise Aihub538PhotometricV4IssuanceError("p6_v4_issuance_directory_replaced")
    @property
    def directory_commitment_sha256(self)->str:
        self._verify_identity(); return sha256_document({"schema_version":"voice_coach.p6_v4_directory_identity.v2","directory_device":self._dir_identity[0],"directory_inode":self._dir_identity[1],"parent_device":self._parent_identity[0],"parent_inode":self._parent_identity[1]})
    @property
    def directory(self)->Path: self._verify_identity(); return self._directory
    def _write_exclusive(self,name:str,document:dict[str,object])->None:
        try: fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600,dir_fd=self._dir_fd)
        except FileExistsError: raise Aihub538PhotometricV4IssuanceError("p6_v4_duplicate_issuance") from None
        try:
            raw=(json.dumps(document,sort_keys=True,separators=(",",":"))+"\n").encode(); written=0
            while written<len(raw): written+=os.write(fd,raw[written:])
            os.fsync(fd)
        finally: os.close(fd)
        os.fsync(self._dir_fd); os.fsync(self._parent_fd)
    def claim(self,*,kind:str,key_sha256:str,payload_sha256:str)->str:
        self._verify_identity(); require_sha256(key_sha256,"key_sha256"); require_sha256(payload_sha256,"payload_sha256")
        if kind not in {"capability","artifact"}: raise ValueError("p6_v4_public_issuance_kind_invalid")
        unsigned={"schema_version":"voice_coach.p6_v4_durable_issuance.v2","kind":kind,"key_sha256":key_sha256,"payload_sha256":payload_sha256,"directory_commitment_sha256":self.directory_commitment_sha256}
        receipt=sha256_document(unsigned)
        try:self._write_exclusive(f"{kind}-{key_sha256}.json",{**unsigned,"receipt_sha256":receipt})
        except Aihub538PhotometricV4IssuanceError: raise Aihub538PhotometricV4IssuanceError(f"p6_v4_duplicate_{kind}_issuance") from None
        return receipt
    def create_reload_worker_challenge(self,artifact_sha256:str,*,expected_worker_pid:int,artifact_file_sha256:str,artifact_identity_sha256:str)->str:
        self._verify_identity(); require_sha256(artifact_sha256,"artifact_sha256"); nonce=secrets.token_hex(32)
        require_sha256(artifact_file_sha256,"artifact_file_sha256"); require_sha256(artifact_identity_sha256,"artifact_identity_sha256")
        worker=Path(__file__).with_name("aihub538_photometric_v4_reload_worker.py"); code_sha=hashlib.sha256(worker.read_bytes()).hexdigest()
        unsigned={"schema_version":"voice_coach.p6_v4_reload_worker_challenge.v1","artifact_sha256":artifact_sha256,"artifact_file_sha256":artifact_file_sha256,"artifact_identity_sha256":artifact_identity_sha256,"nonce_sha256":sha256_document({"nonce":nonce}),"parent_pid":os.getpid(),"expected_worker_pid":expected_worker_pid,"worker_code_sha256":code_sha,"directory_commitment_sha256":self.directory_commitment_sha256}
        self._write_exclusive(f"challenge-{artifact_sha256}.json",{**unsigned,"receipt_sha256":sha256_document(unsigned)})
        return nonce
    def claim_reload_from_worker(self,*,artifact_sha256:str,nonce:str)->str:
        self._verify_identity(); require_sha256(artifact_sha256,"artifact_sha256")
        try:
            fd=os.open(f"challenge-{artifact_sha256}.json",os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=self._dir_fd); raw=os.read(fd,16384); os.close(fd); challenge=json.loads(raw)
        except (OSError,json.JSONDecodeError): raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_worker_challenge_invalid") from None
        code_sha=hashlib.sha256(Path(__file__).with_name("aihub538_photometric_v4_reload_worker.py").read_bytes()).hexdigest()
        if challenge.get("parent_pid")!=os.getppid() or challenge.get("expected_worker_pid")!=os.getpid() or os.getpid()==os.getppid() or challenge.get("worker_code_sha256")!=code_sha or challenge.get("nonce_sha256")!=sha256_document({"nonce":nonce}) or challenge.get("directory_commitment_sha256")!=self.directory_commitment_sha256:
            raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_worker_challenge_invalid")
        unsigned={"schema_version":"voice_coach.p6_v4_durable_issuance.v2","kind":"reload","key_sha256":artifact_sha256,"payload_sha256":artifact_sha256,"artifact_file_sha256":challenge["artifact_file_sha256"],"artifact_identity_sha256":challenge["artifact_identity_sha256"],"directory_commitment_sha256":self.directory_commitment_sha256,"worker_pid":os.getpid(),"parent_pid":os.getppid(),"worker_code_sha256":code_sha,"challenge_receipt_sha256":challenge["receipt_sha256"]}
        receipt=sha256_document(unsigned); self._write_exclusive(f"reload-{artifact_sha256}.json",{**unsigned,"receipt_sha256":receipt}); return receipt
    def validate_fresh_process_reload_receipt(self,value:object,*,artifact_path:Path)->str:
        self._verify_identity(); expected={"schema_version","artifact_sha256","durable_reload_receipt_sha256","fresh_process_reload_required","fresh_process_reload_completed","promotion_authorized","receipt_sha256"}
        if not isinstance(value,dict) or set(value)!=expected or value["schema_version"]!="voice_coach.p6_v4_fresh_process_reload_receipt.v1" or value["fresh_process_reload_required"] is not True or value["fresh_process_reload_completed"] is not True or value["promotion_authorized"] is not False: raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_receipt_invalid")
        artifact=require_sha256(value["artifact_sha256"],"artifact_sha256"); unsigned={k:v for k,v in value.items() if k!="receipt_sha256"}
        if value["receipt_sha256"]!=sha256_document(unsigned): raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_receipt_invalid")
        launch=self._reload_launches.get(artifact)
        if launch is None or launch!=(value["receipt_sha256"],value["durable_reload_receipt_sha256"],True): raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_parent_launch_capability_invalid")
        try: fd=os.open(f"reload-{artifact}.json",os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=self._dir_fd); durable=json.loads(os.read(fd,16384)); os.close(fd)
        except (OSError,json.JSONDecodeError): raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_durable_record_missing") from None
        try:
            metadata=os.stat(artifact_path,follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode): raise OSError("artifact_not_regular")
            file_sha=hashlib.sha256(artifact_path.read_bytes()).hexdigest(); identity_sha=sha256_document({"device":metadata.st_dev,"inode":metadata.st_ino,"size":metadata.st_size,"mtime_ns":metadata.st_mtime_ns})
        except OSError: raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_artifact_identity_invalid") from None
        durable_keys={"schema_version","kind","key_sha256","payload_sha256","artifact_file_sha256","artifact_identity_sha256","directory_commitment_sha256","worker_pid","parent_pid","worker_code_sha256","challenge_receipt_sha256","receipt_sha256"}
        worker=Path(__file__).with_name("aihub538_photometric_v4_reload_worker.py")
        if set(durable)!=durable_keys or durable.get("schema_version")!="voice_coach.p6_v4_durable_issuance.v2" or durable.get("kind")!="reload" or durable.get("key_sha256")!=artifact or durable.get("receipt_sha256")!=sha256_document({k:v for k,v in durable.items() if k!="receipt_sha256"}) or durable.get("receipt_sha256")!=value["durable_reload_receipt_sha256"] or durable.get("payload_sha256")!=artifact or durable.get("artifact_file_sha256")!=file_sha or durable.get("artifact_identity_sha256")!=identity_sha or durable.get("directory_commitment_sha256")!=self.directory_commitment_sha256 or durable.get("worker_pid")==durable.get("parent_pid") or durable.get("worker_code_sha256")!=hashlib.sha256(worker.read_bytes()).hexdigest(): raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_durable_record_invalid")
        try:
            challenge_fd=os.open(f"challenge-{artifact}.json",os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=self._dir_fd); challenge=json.loads(os.read(challenge_fd,16384)); os.close(challenge_fd)
        except (OSError,json.JSONDecodeError): raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_durable_record_invalid") from None
        challenge_keys={"schema_version","artifact_sha256","artifact_file_sha256","artifact_identity_sha256","nonce_sha256","parent_pid","expected_worker_pid","worker_code_sha256","directory_commitment_sha256","receipt_sha256"}
        if set(challenge)!=challenge_keys or challenge.get("schema_version")!="voice_coach.p6_v4_reload_worker_challenge.v1" or challenge.get("receipt_sha256")!=sha256_document({k:v for k,v in challenge.items() if k!="receipt_sha256"}) or challenge.get("receipt_sha256")!=durable.get("challenge_receipt_sha256") or challenge.get("expected_worker_pid")!=durable.get("worker_pid") or challenge.get("parent_pid")!=durable.get("parent_pid") or challenge.get("artifact_sha256")!=artifact or challenge.get("artifact_file_sha256")!=file_sha or challenge.get("artifact_identity_sha256")!=identity_sha or challenge.get("worker_code_sha256")!=durable.get("worker_code_sha256") or challenge.get("directory_commitment_sha256")!=self.directory_commitment_sha256: raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_durable_record_invalid")
        self._write_exclusive(f"reload-consumed-{artifact}.json",{"schema_version":"voice_coach.p6_v4_reload_validation_consumption.v1","artifact_sha256":artifact,"reload_receipt_sha256":value["receipt_sha256"],"directory_commitment_sha256":self.directory_commitment_sha256})
        self._reload_launches.pop(artifact,None)
        return value["receipt_sha256"]
    def launch_fresh_process_reload(self,*,artifact_path:Path,artifact_sha256:str)->dict[str,object]:
        self._verify_identity(); require_sha256(artifact_sha256,"artifact_sha256")
        before=os.stat(artifact_path,follow_symlinks=False); raw=artifact_path.read_bytes(); file_sha=hashlib.sha256(raw).hexdigest()
        identity_sha=sha256_document({"device":before.st_dev,"inode":before.st_ino,"size":before.st_size,"mtime_ns":before.st_mtime_ns})
        read_fd,write_fd=os.pipe2(os.O_CLOEXEC); command=[sys.executable,"-m","voice_coach.visual_feedback.adapters.aihub538_photometric_v4_reload_worker","--artifact",str(artifact_path),"--issuance-directory",str(self.directory),"--challenge-fd",str(read_fd)]
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,pass_fds=(read_fd,),close_fds=True)
        os.close(read_fd)
        try:
            nonce=self.create_reload_worker_challenge(artifact_sha256,expected_worker_pid=process.pid,artifact_file_sha256=file_sha,artifact_identity_sha256=identity_sha); os.write(write_fd,nonce.encode()); os.close(write_fd)
            stdout,stderr=process.communicate(timeout=30)
        except BaseException:
            process.kill(); process.wait(); raise
        finally:
            try:os.close(write_fd)
            except OSError:pass
        after=os.stat(artifact_path,follow_symlinks=False)
        if process.returncode!=0 or stderr or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns): raise Aihub538PhotometricV4IssuanceError("p6_v4_reload_worker_failed")
        response=json.loads(stdout)
        self._reload_launches[artifact_sha256]=(response.get("receipt_sha256"),response.get("durable_reload_receipt_sha256"),process.returncode==0 and bool(stdout) and not stderr)
        return response
__all__=["Aihub538PhotometricV4IssuanceError","Aihub538PhotometricV4IssuanceStore"]
