"""Trusted file-size limiter, invoked behind network_isolated_exec."""
import os
import resource
import sys

if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_FSIZE,(256*1024**2,256*1024**2))
    if len(sys.argv)<2 or sys.argv[1] not in ('/usr/bin/ffmpeg','/usr/bin/ffprobe'):
        raise SystemExit(2)
    os.execv(sys.argv[1],sys.argv[1:])
