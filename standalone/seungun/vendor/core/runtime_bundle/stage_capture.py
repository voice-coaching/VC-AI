"""Symmetric diagnostic only; exact staged gate, never used for latency."""
import json,hashlib,functools,threading,os
from pathlib import Path
ROOT=None
EXPECTED=None
COUNTS={}
LOCK=threading.RLock()
def canonical(x):
    return json.loads(json.dumps(x,ensure_ascii=False,default=str,allow_nan=False))
def check(name,data):
    if int(name[:2])<int(os.environ.get('CAPTURE_MIN_STAGE','0')):return
    data=canonical(data)
    with LOCK:
        n=COUNTS.get(name,0);COUNTS[name]=n+1
        filename=f'{name}_{n:02}.json'
        raw=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        (ROOT/filename).write_text(raw)
        if EXPECTED:
            ref=EXPECTED/filename
            if not ref.exists() or json.loads(ref.read_text())!=data:
                reason={'stage':name,'occurrence':n,'expected':str(ref),'actual':str(ROOT/filename),'reason':'MISSING_ORACLE' if not ref.exists() else 'EXACT_DIFFERENCE'}
                (ROOT/'first_difference.json').write_text(json.dumps(reason,indent=2))
                raise RuntimeError('STAGED_PARITY_FAIL:'+name)
def matrix(m):
    import numpy as np
    a=np.ascontiguousarray(m.numpy() if hasattr(m,'numpy') else m)
    return {'shape':list(a.shape),'dtype':str(a.dtype),'bytes':a.nbytes,'sha256':hashlib.sha256(a.tobytes()).hexdigest()}
def begin(root,expected):
    global ROOT,EXPECTED,COUNTS
    ROOT=Path(root);ROOT.mkdir();EXPECTED=Path(expected) if expected else None;COUNTS={}
def semantic_snapshot(aligner):
    if int(os.environ.get('CAPTURE_MIN_STAGE','0'))>6:return
    from montreal_forced_aligner.db import Dictionary,Word,Pronunciation,Phone,Utterance
    with aligner.session() as s:
        words=s.query(Word).order_by(Word.id).all()
        prons=s.query(Pronunciation).order_by(Pronunciation.id).all()
        def rows(objects,names):return [{k:getattr(o,k) for k in names} for o in objects]
        check('03_pronunciation_phones',rows(prons,['id','word_id','pronunciation']))
        check('04_dictionary_values',{
          'pronunciation':rows(prons,[c.name for c in Pronunciation.__table__.columns]),
          'dictionary':rows(s.query(Dictionary).order_by(Dictionary.id),[c.name for c in Dictionary.__table__.columns if c.name not in ('path','root_temp_directory')])})
        check('05_word_ids',rows(words,[c.name for c in Word.__table__.columns]))
        check('06_phone_ids',rows(s.query(Phone).order_by(Phone.id),[c.name for c in Phone.__table__.columns]))
        check('06_normalized_text',rows(s.query(Utterance).order_by(Utterance.id),['id','kaldi_id','normalized_text','normalized_character_text','oovs','begin','end','channel']))
    tables={}
    for k,l in aligner.lexicon_compilers.items():
        tables[str(k)]={}
        for attr in ('phone_table','word_table'):
            table=getattr(l,attr)
            p=ROOT/f'{k}_{attr}.txt'
            table.write_text(str(p))
            tables[str(k)][attr]=p.read_text()
    check('06_symbol_tables',tables)
def attach(aligner):
    orig=aligner.generate_features
    def features(*a,**k):
        semantic_snapshot(aligner)
        return orig(*a,**k)
    aligner.generate_features=features
def install():
    import _kalpy.feat as feat
    orig=feat.Mfcc.compute
    def raw(*a,**k):
        result=orig(*a,**k);check('07_raw_MFCC',matrix(result));return result
    feat.Mfcc.compute=raw
    import kalpy.gmm.align as ga
    native=ga.gmm_align_compiled
    def decode(*a,**k):
        check('09_transformed_features',matrix(a[3]))
        p=ROOT/'graph.fst';a[2].Write(str(p))
        check('10_graph',{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,'states':a[2].NumStates()})
        check('11_alignment_options',k)
        result=native(*a,**k)
        check('12_transition_ids',list(result[0]))
        check('13_raw_word_ids',list(result[1]))
        check('14_frames',a[3].NumRows())
        check('15_retry',{'success':bool(result[4]),'retry':bool(result[5])})
        return result
    ga.gmm_align_compiled=decode
    from montreal_forced_aligner.corpus.features import MfccFunction,FinalFeatureFunction
    from montreal_forced_aligner.db import Job
    from kalpy.feat.data import FeatureArchive
    def archive_hook(cls,stage):
        original=cls._run
        def run(self):
            result=original(self)
            with self.session() as session:
                job=session.get(Job,self.job_name)
                path=job.construct_path(self.data_directory,'feats','scp')
                archive=FeatureArchive(path)
                try:values=[{'utterance':utt,'matrix':matrix(mat)} for utt,mat in archive]
                finally:archive.close()
            check(stage,values)
            return result
        cls._run=run
    archive_hook(MfccFunction,'08_compressed_reloaded_MFCC')
    archive_hook(FinalFeatureFunction,'09_CMVN_compressed_reloaded')
    from kalpy.gmm.data import Alignment
    ctm_original=Alignment.generate_ctm
    def raw_ctm(*a,**k):
        result=ctm_original(*a,**k)
        check('16_raw_CTM',[{'label':p.label,'begin':p.begin,'end':p.end} for p in result])
        return result
    Alignment.generate_ctm=raw_ctm
    from montreal_forced_aligner.alignment.multiprocessing import AlignmentExtractionFunction
    old=AlignmentExtractionFunction._run
    def collection(self):
        cb=self.callback
        def tap(result):
            utt,did,ctm=result
            check('16_collected_CTM',{'utt':utt,'dictionary':did,'text':ctm.text,'likelihood':ctm.likelihood,
              'words':[{'label':w.label,'begin':w.begin,'end':w.end,'phones':[{'label':p.label,'begin':p.begin,'end':p.end} for p in w.phones]} for w in ctm.word_intervals]})
            return cb(result)
        self.callback=tap
        try:return old(self)
        finally:self.callback=cb
    AlignmentExtractionFunction._run=collection
