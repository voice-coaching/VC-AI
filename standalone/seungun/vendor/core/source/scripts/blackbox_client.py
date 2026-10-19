"""Thin, request-ID-checked client for separately launched historical MFA."""
from __future__ import annotations
import json, socket
class BlackBoxMFAClient:
    def __init__(self,path):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(165.0)
        self.sock.connect(str(path))
        self.buf=b""
        self.closed=False
    def call(self,op,**kwargs):
        req={"op":op,**kwargs}
        self.sock.sendall(json.dumps(req,ensure_ascii=False).encode()+b"\n")
        while b"\n" not in self.buf:
            chunk=self.sock.recv(65536)
            if not chunk:raise RuntimeError("BLACKBOX_SIDECAR_EOF")
            self.buf+=chunk
            if len(self.buf)>8388608:raise RuntimeError("BLACKBOX_RESPONSE_TOO_LARGE")
        line,self.buf=self.buf.split(b"\n",1)
        response=json.loads(line)
        if not response.get("ok"):raise RuntimeError(response.get("error","BLACKBOX_SIDECAR_FAILED"))
        result=response["result"]
        if op=="resident" and result.get("request_id")!=kwargs.get("request_id"):
            raise RuntimeError("BLACKBOX_REQUEST_ID_MISMATCH")
        return result
    def close(self):
        if not self.closed:self.sock.close();self.closed=True
