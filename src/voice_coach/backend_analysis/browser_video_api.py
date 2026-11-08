"""Browser-only original upload surface; Backend public-key authorization."""
import asyncio
import hashlib
import logging
import os
from secrets import token_hex

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from voice_coach.direct_analysis.domain import DirectFailure
from .browser_video_grants import GrantVerifier, binding
from .http_models import HttpFailure, parse_json

PREFIX = '/browser/v1/video-analysis-jobs'
METHODS = 'GET, POST, PUT, OPTIONS'
HEADERS = {'authorization','content-type'}


class BrowserVideoAPI:
    def __init__(self, app, runtime, environment):
        self.runtime, self.verifier, self.uploads = runtime, None, 0
        try:
            if runtime is None: raise ValueError('independent executor required')
            self.verifier = GrantVerifier(environment)
        except Exception as error:
            logging.getLogger(__name__).warning('browser_video_configuration_failed exceptionType=%s',type(error).__name__)
        self.install(app)

    def handles(self, path):
        return path == PREFIX or path.startswith(PREFIX + '/')

    def cors(self, request):
        origin = request.headers.get('origin')
        if (self.verifier is None): raise HttpFailure(503,'BROWSER_VIDEO_NOT_CONFIGURED')
        if len(request.headers.getlist('origin')) != 1 or origin not in self.verifier.origins:
            raise HttpFailure(403,'ORIGIN_NOT_ALLOWED')
        return {'Access-Control-Allow-Origin':origin,'Vary':'Origin',
                'Access-Control-Expose-Headers':'X-Result-SHA256, ETag, Retry-After'}

    def authorize(self, request):
        values = request.headers.getlist('authorization')
        if len(values) != 1: raise HttpFailure(401,'INVALID_UPLOAD_GRANT')
        request.state.video_grant = self.verifier.verify(values[0],request.headers.get('origin'))

    def preflight(self, request):
        requested = request.headers.get('access-control-request-method','')
        headers = {h.strip().lower() for h in request.headers.get('access-control-request-headers','').split(',') if h.strip()}
        if requested not in ('GET','POST','PUT') or not headers <= HEADERS:
            raise HttpFailure(403,'CORS_REQUEST_NOT_ALLOWED')
        return Response(status_code=204,headers={'Access-Control-Allow-Methods':METHODS,
            'Access-Control-Allow-Headers':'Authorization, Content-Type','Access-Control-Max-Age':'300'})

    def owned(self, request, job_id):
        store = self.runtime.require_store()
        try: doc = store.read(job_id)
        except DirectFailure as error: raise HttpFailure(error.status,error.code) from None
        if doc.get('browserBinding') != binding(request.state.video_grant):
            # Do not disclose another user's job, even when its UUID is known.
            raise HttpFailure(404,'NOT_FOUND')
        return doc

    async def invoke(self, fn, *args):
        try: return await asyncio.to_thread(fn,*args)
        except DirectFailure as error: raise HttpFailure(error.status,error.code) from None

    def install(self, app):
        def reply(d, status=200):
            return JSONResponse(d,status_code=status,headers={'Cache-Control':'no-store'})

        @app.post(PREFIX)
        async def create(request:Request):
            from .independent_av_api import control_body
            value = parse_json(await control_body(request,65536,'application/json'))
            return reply(await self.invoke(self.runtime.create_original,value,request.state.video_grant),202)

        @app.get(PREFIX)
        async def find(request:Request, clientRequestId:str):
            if clientRequestId != request.state.video_grant['clientRequestId']: raise HttpFailure(404,'NOT_FOUND')
            store = self.runtime.require_store()
            job = store.attempts.get(clientRequestId)
            if job is None: raise HttpFailure(404,'NOT_FOUND')
            self.owned(request,job)
            return reply(store.view(job))

        @app.get(PREFIX+'/{job_id}')
        async def state(job_id:str,request:Request):
            self.owned(request,job_id)
            return reply(self.runtime.require_store().view(job_id))

        @app.put(PREFIX+'/{job_id}/source')
        async def upload(job_id:str,request:Request):
            doc = self.owned(request,job_id)
            spec = doc['metadata']['source']
            if (request.headers.get('content-type','').split(';')[0].strip() != spec['mimeType']
                    or request.headers.get('content-encoding','identity') != 'identity'):
                raise HttpFailure(415,'UNSUPPORTED_MEDIA_TYPE')
            if self.uploads >= 2: raise HttpFailure(429,'UPLOAD_CAPACITY_EXCEEDED')
            self.uploads += 1
            store = self.runtime.require_store()
            temp = store.directory(job_id)/('upload-'+token_hex(16)+'.tmp')
            try:
                size, pin = 0, hashlib.sha256()
                with os.fdopen(os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:
                    async with asyncio.timeout(120):
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > spec['fileSizeBytes']: raise HttpFailure(413,'PAYLOAD_TOO_LARGE')
                            pin.update(chunk)
                            await asyncio.to_thread(stream.write,chunk)
                        await asyncio.to_thread(stream.flush)
                        await asyncio.to_thread(os.fsync,stream.fileno())
                await self.invoke(store.upload_original_file,job_id,temp,size,pin.hexdigest())
                return reply(store.view(job_id))
            except TimeoutError: raise HttpFailure(408,'UPLOAD_TIMEOUT') from None
            finally:
                temp.unlink(missing_ok=True)
                self.uploads -= 1

        @app.post(PREFIX+'/{job_id}/start')
        async def start(job_id:str,request:Request):
            self.owned(request,job_id)
            return reply(await self.invoke(self.runtime.enqueue,job_id),202)

        @app.get(PREFIX+'/{job_id}/result')
        async def result(job_id:str,request:Request):
            self.owned(request,job_id)
            raw,pin = await self.invoke(self.runtime.require_store().result,job_id)
            return Response(raw,media_type='application/json',headers={'X-Result-SHA256':pin,'ETag':'"'+pin+'"'})

        @app.get(PREFIX+'/{job_id}/summary')
        async def summary(job_id:str,request:Request):
            from .browser_video_summary import summarize
            self.owned(request,job_id)
            raw,_ = await self.invoke(self.runtime.require_store().result,job_id)
            payload,pin = summarize(raw)
            return Response(payload,media_type='application/json',headers={
                'Cache-Control':'no-store','X-Result-SHA256':pin,'ETag':'"'+pin+'"'})

        @app.post(PREFIX+'/{job_id}/cancel')
        async def cancel(job_id:str,request:Request):
            self.owned(request,job_id)
            return reply(await self.invoke(self.runtime.cancel,job_id))
