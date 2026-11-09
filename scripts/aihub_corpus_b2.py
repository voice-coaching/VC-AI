"""Store AI-Hub raw/derived assets in a private B2 prefix, verifying full download.

Credentials are entered through hidden stdin, never arguments or a receipt.
The local intent/receipt makes an ambiguous publication stop without re-PUT.
No existing object/version, unfinished upload or local data is deleted.
"""
import argparse
import base64
import getpass
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy/dlpc'));sys.path.insert(0,str(ROOT/'src'))
from b2_backup import B2, checked_url
from voice_coach.visual_feedback.corpus_dataset import private_write,file_sha
from voice_coach.visual_feedback.corpus_reference import encode,require,sha

PREFIX='datasets/aihub/538/lip-population-v1/'
PART=64*1024**2


def authorize(bucket_name):
    key_id=getpass.getpass('B2 key ID: ');key=getpass.getpass('B2 application key: ')
    basic=base64.b64encode((key_id+':'+key).encode()).decode()
    request=urllib.request.Request('https://api.backblazeb2.com/b2api/v4/b2_authorize_account',
        headers={'Authorization':'Basic '+basic})
    with urllib.request.urlopen(request,timeout=30) as reply:auth=json.load(reply)
    api=auth['apiInfo']['storageApi']
    client=B2(dict(apiUrl=api['apiUrl'],downloadUrl=api['downloadUrl'],
        authorizationToken=auth['authorizationToken'],accountId=auth['accountId'],
        bucketId='',bucketName=bucket_name))
    buckets=client.call('b2_list_buckets',dict(accountId=auth['accountId'],bucketName=bucket_name))['buckets']
    require(len(buckets)==1 and buckets[0]['bucketType']=='allPrivate','CORPUS_PRIVATE_BUCKET_REQUIRED')
    client.bucket=buckets[0]['bucketId'];return client


def upload(path,receipt_path,client,kind):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size>0)
    require(kind in {'raw','derived'})
    digest=file_sha(path);size=path.stat().st_size
    name=PREFIX+kind+'/'+digest+'/'+path.name
    require(path.name not in {'.env','credentials','secrets'} and path.suffix not in {'.key','.env'},
            'CORPUS_SECRET_FILE_FORBIDDEN')
    intent=receipt_path.with_suffix('.intent.json')
    require(not receipt_path.exists() and not intent.exists(),'CORPUS_UPLOAD_ALREADY_ATTEMPTED')
    prior=client.call('b2_list_file_names',dict(bucketId=client.bucket,prefix=name,maxFileCount=1))['files']
    require(not prior or prior[0]['fileName']!=name,'CORPUS_B2_OBJECT_EXISTS')
    private_write(intent,encode(dict(name=name,bytes=size,sha256=digest)))
    if size<=PART:
        item=client.upload_manifest(name,path.read_bytes());file_id=item['fileId']
    else:
        item=client.call('b2_start_large_file',dict(bucketId=client.bucket,fileName=name,
            contentType='application/octet-stream',fileInfo={'sha256':digest}))
        file_id=item['fileId'];parts=[]
        private_write(receipt_path.with_suffix('.large-file.json'),encode(dict(fileId=file_id,fileName=name)))
        with path.open('rb') as stream:
            while raw:=stream.read(PART):
                parts.append(client.upload_part(file_id,len(parts)+1,raw)['sha1'])
        item=client.call('b2_finish_large_file',dict(fileId=file_id,partSha1Array=parts))
        require(item['fileName']==name and item['contentLength']==size,'CORPUS_B2_SIZE_MISMATCH')
    checksum=hashlib.sha256();downloaded=0
    with client.read(file_id) as stream:
        while chunk:=stream.read(8*1024**2):checksum.update(chunk);downloaded+=len(chunk)
    require(downloaded==size and checksum.hexdigest()==digest and file_sha(path)==digest,
            'CORPUS_B2_DOWNLOAD_MISMATCH')
    receipt=dict(schemaVersion='voice-coaching.aihub538-b2-receipt.v1',datasetId='538',
        bucketName=client.name,fileName=name,fileId=file_id,bytes=size,sha256=digest,
        verification='FULL_DOWNLOAD_SHA256',providerPosts=0)
    private_write(receipt_path,encode(receipt));return dict(status='VERIFIED',bytes=size,sha256=digest)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--bucket',default='speakai2026');p.add_argument('--kind',choices=('raw','derived'),required=True)
    a=p.parse_args()
    try:print(json.dumps(upload(a.file,a.receipt,authorize(a.bucket),a.kind)))
    except Exception as error:
        print(json.dumps(dict(status='FAILED_OR_RECONCILIATION_REQUIRED',exceptionType=type(error).__name__)))
        raise SystemExit(2)
