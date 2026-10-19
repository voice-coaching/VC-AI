import sys, os, pathlib
ROOT=pathlib.Path('/workspace/intelligentai/validation/finish_20260916/latency_optimization_20260921T044013_2c3be9/mfa_startup_recovery_20260921T065201_ac461c/cpu_parallel_limited_regression_20260923T223450Z_e5XbK3/attempt02/evacuation_sequential').resolve()
LOCAL=pathlib.Path(os.environ["RECOVERY_LOCAL"]).resolve()
def audit(event,args):
    if event=="socket.connect":
        address=args[1]
        if isinstance(address,tuple) and address[0] not in ("127.0.0.1","::1","localhost"):
            raise PermissionError("benchmark network is offline")
    if event=="socket.getaddrinfo" and args[0] not in ("127.0.0.1","::1","localhost",None):
        raise PermissionError("benchmark DNS is offline")
    if event=="open" and isinstance(args[0],(str,bytes,os.PathLike)):
        mode=args[1] or ""; flags=args[2]
        write=any(c in str(mode) for c in "wax+") or isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC)
        if write:
            p=pathlib.Path(os.fsdecode(args[0])).resolve()
            if str(p) not in ("/dev/null","/dev/tty") and not (p.is_relative_to(ROOT) or p.is_relative_to(LOCAL)):
                raise PermissionError("benchmark write outside authorized roots: "+str(p))
sys.addaudithook(audit)
os.environ["RECOVERY_GUARD_ACTIVE"]="1"
