"""Authenticated video-specific API. Audio-only independent routes are untouched."""
import asyncio
import hashlib
from pathlib import Path
import re

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from voice_coach.direct_analysis.domain import DirectFailure
from .http_models import HttpFailure, parse_json
from .independent_av_store import ARTIFACTS

PREFIX='/v2/audiovisual-analysis-jobs'


def install_routes(app, runtime):
    app.state.audiovisual_uploads=0

    async def invoke(fn,*args):
        try:return await asyncio.to_thread(fn,*args)
        except DirectFailure as error:raise HttpFailure(error.status,error.code) from None

    async def body(request,limit,content_type):
        if (request.headers.get('content-type','').split(';')[0].strip()!=content_type
                or request.headers.get('content-encoding','identity')!='identity'):
            raise HttpFailure(415,'UNSUPPORTED_MEDIA_TYPE')
        raw=bytearray()
        try:
            async with asyncio.timeout(120):
                async for part in request.stream():
                    if len(raw)+len(part)>limit:raise HttpFailure(413,'PAYLOAD_TOO_LARGE')
                    raw.extend(part)
        except TimeoutError:raise HttpFailure(408,'UPLOAD_TIMEOUT') from None
        return bytes(raw)

    def reply(value,status=200):
        return JSONResponse(value,status_code=status,headers={'Cache-Control':'no-store'})

    @app.post(PREFIX)
    async def create(request:Request):
        value=parse_json(await body(request,65536,'application/json'))
        return reply(await invoke(runtime.create,value),202)

    @app.get(PREFIX)
    async def find(clientRequestId:str):
        store=runtime.require_store()
        job=store.attempts.get(clientRequestId)
        if job is None:raise HttpFailure(404,'NOT_FOUND')
        return reply(await invoke(store.view,job))

    @app.get(PREFIX+'/{job_id}')
    async def state(job_id:str):
        return reply(await invoke(runtime.require_store().view,job_id))

    @app.put(PREFIX+'/{job_id}/media/{kind}')
    async def upload(job_id:str,kind:str,request:Request):
        if kind not in ('audio','video'):raise HttpFailure(404,'NOT_FOUND')
        store=runtime.require_store()
        doc=await invoke(store.read,job_id)
        declared=doc['metadata'][kind]
        if app.state.audiovisual_uploads>=2:raise HttpFailure(429,'UPLOAD_CAPACITY_EXCEEDED')
        app.state.audiovisual_uploads+=1
        try:
            raw=await body(request,declared['fileSizeBytes'],declared['mimeType'])
            await invoke(store.upload,job_id,kind,raw)
            return reply(await invoke(store.view,job_id))
        finally:app.state.audiovisual_uploads-=1

    @app.post(PREFIX+'/{job_id}/start')
    async def start(job_id:str):
        return reply(await invoke(runtime.enqueue,job_id),202)

    @app.get(PREFIX+'/{job_id}/result')
    async def result(job_id:str):
        raw,pin=await invoke(runtime.require_store().result,job_id)
        return Response(raw,media_type='application/json',headers={
            'Cache-Control':'no-store','ETag':'"'+pin+'"','X-Result-SHA256':pin})

    @app.get(PREFIX+'/{job_id}/artifacts/{kind}')
    async def artifact(job_id:str,kind:str):
        if kind not in ARTIFACTS:raise HttpFailure(404,'NOT_FOUND')
        store=runtime.require_store()
        doc=await invoke(store.read,job_id)
        if doc['status'] not in ('COMPLETED','FAILED'):raise HttpFailure(409,'RESULT_NOT_AVAILABLE')
        path=store.directory(job_id)/'artifacts'/(kind+'.json')
        if not path.is_file():raise HttpFailure(404,'NOT_FOUND')
        raw=await asyncio.to_thread(path.read_bytes)
        return Response(raw,media_type='application/json',headers={
            'Cache-Control':'no-store','X-Artifact-SHA256':hashlib.sha256(raw).hexdigest()})

    @app.post(PREFIX+'/{job_id}/ack')
    async def ack(job_id:str,request:Request):
        d=parse_json(await body(request,1024,'application/json'))
        if (type(d) is not dict or set(d)!={'resultSha256'} or type(d['resultSha256']) is not str
                or re.fullmatch('[a-f0-9]{64}',d['resultSha256']) is None):
            raise HttpFailure(422,'VALIDATION_FAILED')
        return reply(await invoke(runtime.acknowledge,job_id,d['resultSha256']))

    @app.post(PREFIX+'/{job_id}/cancel')
    async def cancel(job_id:str):
        return reply(await invoke(runtime.cancel,job_id))
