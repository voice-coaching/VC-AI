"""Select full frontal expert MP4s from an official uncompressed TAR using Range.

Only label-approved filenames are copied; entire all-angle 50-100GB bundles
need not be materialized. This is SELECTIVE acquisition, not a whole-bundle
download receipt. The normal downloader's authorization/redirect rules apply.
"""
import argparse
import bisect
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
from urllib.request import Request,build_opener

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from download_aihub_secure import _PinnedOriginRedirectHandler,_aihub538_source_authorization_guard
from voice_coach.visual_feedback.corpus_dataset import private_write,file_sha,ordered_tar_parts
from voice_coach.visual_feedback.corpus_reference import encode,require,sha
from voice_coach.visual_feedback.feature_protocol import strict_json


class RemoteTar(io.RawIOBase):
    """Bounded cached reads; nonzero Range must be acknowledged exactly."""
    def __init__(self,key,file_key):
        self.key=key;self.file_key=file_key;self.position=0;self.cache=b'';self.cache_at=-1
        self.opener=build_opener(_PinnedOriginRedirectHandler());self.network_bytes=0
        self.recheck=_aihub538_source_authorization_guard(argparse.Namespace(dataset_key='538',file_keys=file_key))
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        require(whence in (0,1),'CORPUS_RANGE_SEEK_UNSUPPORTED')
        self.position=offset if whence==0 else self.position+offset
        require(self.position>=0);return self.position
    def read(self,n=-1):
        require(0<=n<=8*1024**2,'CORPUS_RANGE_READ_LIMIT')
        result=bytearray()
        while len(result)<n:
            if not self.cache_at<=self.position<self.cache_at+len(self.cache):
                self.recheck()
                request=Request('https://api.aihub.or.kr/down/0.6/538.do?fileSn='+self.file_key,
                    headers={'apikey':self.key,'Range':f'bytes={self.position}-','Accept-Encoding':'identity'})
                with self.opener.open(request,timeout=60) as reply:
                    content_range=reply.headers.get('Content-Range','')
                    require((self.position==0 and reply.status==200) or
                        (reply.status==206 and re.fullmatch(r'bytes '+str(self.position)+r'-[0-9]+/[0-9]+',content_range)),
                        'CORPUS_RANGE_NOT_ACKNOWLEDGED')
                    self.cache=reply.read(1024**2);self.cache_at=self.position
                    self.network_bytes+=len(self.cache)
                if not self.cache:break
            offset=self.position-self.cache_at;take=min(n-len(result),len(self.cache)-offset)
            result.extend(self.cache[offset:offset+take]);self.position+=take
        return bytes(result)


class Parts(io.RawIOBase):
    def __init__(self,remote,parts):
        self.remote=remote;self.parts=parts;self.starts=[0];self.position=0
        for p in parts:self.starts.append(self.starts[-1]+p.size)
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        require(whence in (0,1));self.position=offset if whence==0 else self.position+offset
        require(self.position>=0);return self.position
    def read(self,n=-1):
        require(0<=n<=8*1024**2);result=bytearray()
        while len(result)<n and self.position<self.starts[-1]:
            i=bisect.bisect_right(self.starts,self.position)-1;p=self.parts[i]
            offset=self.position-self.starts[i];self.remote.seek(p.offset_data+offset)
            raw=self.remote.read(min(n-len(result),p.size-offset))
            require(bool(raw),'CORPUS_SOURCE_TRUNCATED');result.extend(raw);self.position+=len(raw)
        return bytes(result)


def select_labels(root):
    selected={};seen=set()
    for label in sorted(root.rglob('*.json')):
        v=strict_json(label.read_bytes());require(type(v) is list and len(v)==1)
        v=v[0]
        if (v['speaker_info']['Specificity'],v['Audio_env']['Noise'],v['Video_env']['Angle'])!=('E',1,'A'):continue
        speaker=sha(('aihub538:speaker:'+v['speaker_info']['speaker_ID']).encode())
        if speaker in seen:continue
        seen.add(speaker);name=v['Video_info']['video_Name']
        require(Path(name).name==name and name.endswith('.mp4') and name not in selected)
        selected[name]=dict(speakerSha256=speaker,labelSha256=file_sha(label))
    return selected


def collect(key_path,file_key,labels,output,maximum_videos,maximum_bytes,stream=False):
    require(re.fullmatch('[1-9][0-9]*',file_key) and maximum_videos>0 and maximum_bytes>0)
    info=key_path.lstat();require(not key_path.is_symlink() and info.st_mode&0o077==0,'PRIVATE_AI_HUB_KEY_REQUIRED')
    lines=key_path.read_text().splitlines();keys=[r.partition('=')[2] for r in lines if r.startswith('AIHUB_API_KEY=')]
    require(len(keys)==1 and bool(keys[0]))
    names=dict(list(select_labels(labels).items())[:maximum_videos]);require(bool(names),'CORPUS_EXPERT_LABELS_REQUIRED')
    # Reuse only completed, content-verified source files. A partial MP4 without
    # its acquisition receipt is ambiguous and is never overwritten.
    output.mkdir(parents=True,mode=0o700,exist_ok=True)
    remote=RemoteTar(keys[0],file_key);entries=[];total=0
    if stream:
        return stream_collect(remote,names,output,maximum_bytes)
    with tarfile.open(fileobj=remote,mode='r:') as outer:
        parts=ordered_tar_parts([m for m in outer.getmembers() if m.isfile()])
        with tarfile.open(fileobj=Parts(remote,parts),mode='r:') as inner:
            for m in inner:
                name=Path(m.name).name
                if name not in names:continue
                require(m.isfile() and 0<m.size and total+m.size<=maximum_bytes,'CORPUS_ACQUISITION_BUDGET')
                require(name not in {r['fileName'] for r in entries},'CORPUS_DUPLICATE_VIDEO')
                checksum=hashlib.sha256();path=output/name
                with inner.extractfile(m) as source,os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as dest:
                    while raw:=source.read(1024**2):dest.write(raw);checksum.update(raw)
                require(path.stat().st_size==m.size,'CORPUS_SOURCE_TRUNCATED')
                entries.append(dict(fileName=name,bytes=m.size,sha256=checksum.hexdigest(),**names[name]));total+=m.size
                private_write(output/(name+'.acquisition.json'),encode(dict(
                    schemaVersion='voice-coaching.aihub538-selective-acquisition.v1',datasetId='538',
                    fileKey=file_key,asset=entries[-1],wholeArchiveVerified=False,providerPosts=0)))
                print(json.dumps(dict(event='source_video_saved',count=len(entries),bytes=total)),flush=True)
                if len(entries)==len(names):break
    require(len(entries)==len(names),'CORPUS_SELECTED_VIDEO_MISSING')
    receipt=dict(schemaVersion='voice-coaching.aihub538-selective-acquisition.v1',datasetId='538',
        fileKey=file_key,assets=entries,wholeArchiveVerified=False,networkBytes=remote.network_bytes,providerPosts=0)
    private_write(output/'acquisition-receipt.json',encode(receipt));return dict(status='SELECTIVE_ACQUISITION_COMPLETE',
        videoCount=len(entries),bytes=total,networkBytes=remote.network_bytes,providerPosts=0)


def stream_collect(remote,names,output,maximum_bytes):
    """Explicit sequential mode when the service ignores nonzero HTTP Range."""
    remote.recheck()
    request=Request('https://api.aihub.or.kr/down/0.6/538.do?fileSn='+remote.file_key,
        headers={'apikey':remote.key,'Accept-Encoding':'identity'})
    entries=[];total=0
    with remote.opener.open(request,timeout=60) as reply:
        require(reply.status==200,'CORPUS_SOURCE_HTTP_STATUS')
        class Counted:
            def read(self,n):
                raw=reply.read(n);remote.network_bytes+=len(raw);return raw
        with tarfile.open(fileobj=Counted(),mode='r|*') as outer:
            first=outer.next();require(first is not None and first.isfile() and first.name.endswith('.tar.part0'))
            base=first.name.removesuffix('.part0')
            class Joined(io.RawIOBase):
                def __init__(self):
                    self.index=0;self.offset=first.size;self.mode=None;self.part=outer.extractfile(first);self.finished=False
                def readable(self):return True
                def readinto(self,buffer):
                    while not self.finished:
                        raw=self.part.read(len(buffer))
                        if raw:buffer[:len(raw)]=raw;return len(raw)
                        self.part.close();self.index+=1;member=outer.next()
                        if member is None:self.finished=True;return 0
                        if self.mode is None:
                            self.mode='OFFSET' if member.name==base+'.part'+str(self.offset) else 'ORDINAL'
                        expected=self.offset if self.mode=='OFFSET' else self.index
                        require(member.isfile() and member.name==base+'.part'+str(expected),'CORPUS_ARCHIVE_PART_MISSING')
                        self.offset+=member.size
                        self.part=outer.extractfile(member)
                    return 0
            with io.BufferedReader(Joined()) as joined,tarfile.open(fileobj=joined,mode='r|*') as inner:
                for m in inner:
                    name=Path(m.name).name
                    if name not in names:continue
                    require(m.isfile() and 0<m.size and total+m.size<=maximum_bytes,
                            'CORPUS_ACQUISITION_BUDGET')
                    require(name not in {r['fileName'] for r in entries},'CORPUS_DUPLICATE_VIDEO')
                    checksum=hashlib.sha256();path=output/name
                    saved=path.with_name(name+'.acquisition.json')
                    if path.exists():
                        old=strict_json(saved.read_bytes())
                        require(old['fileKey']==remote.file_key and old['asset']['bytes']==m.size
                                and old['asset']['sha256']==file_sha(path),'CORPUS_SOURCE_CHANGED')
                        entries.append(old['asset']);total+=m.size
                        if len(entries)==len(names):break
                        continue
                    with inner.extractfile(m) as source,os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as dest:
                        while raw:=source.read(1024**2):dest.write(raw);checksum.update(raw)
                    require(path.stat().st_size==m.size,'CORPUS_SOURCE_TRUNCATED')
                    entries.append(dict(fileName=name,bytes=m.size,sha256=checksum.hexdigest(),**names[name]));total+=m.size
                    private_write(output/(name+'.acquisition.json'),encode(dict(
                        schemaVersion='voice-coaching.aihub538-selective-acquisition.v1',datasetId='538',
                        fileKey=remote.file_key,asset=entries[-1],wholeArchiveVerified=False,providerPosts=0)))
                    print(json.dumps(dict(event='source_video_saved',count=len(entries),bytes=total)),flush=True)
                    if len(entries)==len(names):break
    require(len(entries)==len(names),'CORPUS_SELECTED_VIDEO_MISSING')
    receipt=dict(schemaVersion='voice-coaching.aihub538-selective-acquisition.v1',datasetId='538',
        fileKey=remote.file_key,assets=entries,wholeArchiveVerified=False,networkBytes=remote.network_bytes,providerPosts=0)
    private_write(output/'acquisition-receipt.json',encode(receipt))
    return dict(status='SELECTIVE_ACQUISITION_COMPLETE',videoCount=len(entries),bytes=total,
                networkBytes=remote.network_bytes,providerPosts=0)


def from_download(archive,download_receipt,labels,output,maximum_videos,maximum_bytes):
    """Join numerically ordered parts even when the server's TAR lists 1,10,2.

The official completed archive is verified in full; only selected MP4s are
written. Existing completed selected files must match the archive exactly.
"""
    receipt_raw=download_receipt.read_bytes();receipt=strict_json(receipt_raw)
    require(receipt.get('schema_version')=='voice_coach.aihub_secure_download_receipt.v1'
        and receipt['dataset_key']=='538' and len(receipt['file_keys'])==1,'CORPUS_DOWNLOAD_RECEIPT_REQUIRED')
    require(archive.stat().st_size==receipt['archive_size_bytes'] and file_sha(archive)==receipt['archive_sha256'],
            'CORPUS_DOWNLOAD_SHA_MISMATCH')
    names=dict(list(select_labels(labels).items())[:maximum_videos]);require(bool(names))
    output.mkdir(parents=True,mode=0o700,exist_ok=True);entries=[];total=0
    with tarfile.open(archive,'r:') as outer:
        parts=ordered_tar_parts([p for p in outer.getmembers() if p.isfile()])
        with tarfile.open(fileobj=Parts(outer.fileobj,parts),mode='r:') as inner:
            for m in inner:
                name=Path(m.name).name
                if name not in names:continue
                require(m.isfile() and 0<m.size and total+m.size<=maximum_bytes,'CORPUS_ACQUISITION_BUDGET')
                require(name not in {r['fileName'] for r in entries},'CORPUS_DUPLICATE_VIDEO')
                path=output/name;checksum=hashlib.sha256();existing=path.exists()
                if existing:
                    old=strict_json((output/(name+'.acquisition.json')).read_bytes())
                    require(old['fileKey']==receipt['file_keys'][0] and file_sha(path)==old['asset']['sha256'])
                target=output/('.'+name+'.partial')
                with inner.extractfile(m) as source:
                    if existing:
                        while raw:=source.read(1024**2):checksum.update(raw)
                        require(checksum.hexdigest()==file_sha(path),'CORPUS_SOURCE_CHANGED')
                    else:
                        with os.fdopen(os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as dest:
                            while raw:=source.read(1024**2):dest.write(raw);checksum.update(raw)
                            dest.flush();os.fsync(dest.fileno())
                        require(target.stat().st_size==m.size,'CORPUS_SOURCE_TRUNCATED')
                        os.link(target,path);target.unlink()
                asset=dict(fileName=name,bytes=m.size,sha256=checksum.hexdigest(),**names[name]);entries.append(asset);total+=m.size
                if not existing:private_write(output/(name+'.acquisition.json'),encode(dict(
                    schemaVersion='voice-coaching.aihub538-selective-acquisition.v1',datasetId='538',
                    fileKey=receipt['file_keys'][0],asset=asset,archiveSha256=receipt['archive_sha256'],
                    wholeArchiveVerified=True,providerPosts=0)))
                if len(entries)==len(names):break
    require(len(entries)==len(names),'CORPUS_SELECTED_VIDEO_MISSING')
    result=dict(schemaVersion='voice-coaching.aihub538-selective-acquisition.v1',datasetId='538',
        fileKey=receipt['file_keys'][0],assets=entries,wholeArchiveVerified=True,
        archiveSha256=receipt['archive_sha256'],downloadReceiptSha256=sha(receipt_raw),providerPosts=0)
    private_write(output/'verified-archive-acquisition.json',encode(result))
    return dict(status='ARCHIVE_FRONTALS_VERIFIED',videoCount=len(entries),bytes=total,providerPosts=0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--key-file',type=Path);p.add_argument('--file-key')
    p.add_argument('--local-archive',type=Path);p.add_argument('--download-receipt',type=Path)
    p.add_argument('--labels',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--maximum-videos',type=int,required=True);p.add_argument('--maximum-bytes',type=int,required=True)
    p.add_argument('--stream',action='store_true')
    a=p.parse_args()
    try:
        if a.local_archive:
            require(a.download_receipt is not None)
            result=from_download(a.local_archive,a.download_receipt,a.labels,a.output,a.maximum_videos,a.maximum_bytes)
        else:
            require(a.key_file is not None and a.file_key is not None)
            result=collect(a.key_file,a.file_key,a.labels,a.output,a.maximum_videos,a.maximum_bytes,a.stream)
        print(json.dumps(result))
    except Exception as error:
        code=str(error) if str(error).startswith('CORPUS_') else 'CORPUS_ACQUISITION_FAILED'
        print(json.dumps(dict(status='FAILED_PARTIAL_RETAINED',exceptionType=type(error).__name__,code=code)));raise SystemExit(2)
