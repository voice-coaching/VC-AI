"""Experimental single-utterance path. No site-package changes; flag defaults OFF.
Retains request-local semantic DB and all existing numerical/serialization APIs.
"""
from pathlib import Path
import datetime
from montreal_forced_aligner.alignment import PretrainedAligner
from montreal_forced_aligner import config
from montreal_forced_aligner.corpus import acoustic_corpus as ac
from montreal_forced_aligner.alignment import multiprocessing as mp, mixins as mix
from montreal_forced_aligner.db import Job, Utterance, Speaker, Phone, CorpusWorkflow
from montreal_forced_aligner.data import PhoneType, WorkflowType

class DirectAligner(PretrainedAligner):
    def _load_corpus_from_source_mp(self):
        assert config.NUM_JOBS==1 and config.USE_MP and config.USE_THREADING and config.SINGLE_SPEAKER
        root=Path(self.corpus_directory)
        files=sorted(root.iterdir())
        assert len(files)==2 and all(p.is_file() and not p.is_symlink() for p in files)
        wav=next(p for p in files if p.suffix=='.wav')
        lab=wav.with_suffix('.lab')
        assert lab in files and not self.audio_directory
        # Exact arguments of AcousticDirectoryParser + CorpusProcessWorker.
        file=ac.FileData.parse_file(wav.stem,str(wav),str(lab),'',self.speaker_characters)
        with self.session() as session:
            data=ac.DatabaseImportData()
            data.add_objects(self.generate_import_objects(file))
            self._finalize_load(session,data)

    def compile_train_graphs(self):
        assert config.NUM_JOBS==1 and not self.uses_speaker_adaptation
        with self.session() as session:
            workflow=session.query(CorpusWorkflow).filter(CorpusWorkflow.current==True).first()
            assert workflow.workflow_type is not WorkflowType.transcript_verification
            for job in session.query(Job).order_by(Job.id):
                for d in job.training_dictionaries:
                    lexicon=self.lexicon_compilers[d.id] if d.id in self.lexicon_compilers else d.lexicon_compiler
                    compiler=mp.TrainingGraphCompiler(self.alignment_model_path,self.working_directory/'tree',lexicon,batch_size=500)
                    query=(session.query(Utterance.kaldi_id,Utterance.normalized_text)
                           .join(Utterance.speaker).filter(Utterance.job_id==job.id,Speaker.dictionary_id==d.id)
                           .filter(Utterance.ignored==False).order_by(Utterance.kaldi_id))
                    if job.corpus.current_subset>0: query=query.filter(Utterance.in_subset==True)
                    assert query.count()==1
                    compiler.export_graphs(job.construct_path(workflow.working_directory,'fsts','ark',d.name),
                                           query,interjection_words={})
                    del compiler,lexicon

    def align_utterances(self,training=False):
        assert not training and config.NUM_JOBS==1 and not self.uses_speaker_adaptation
        results=[]
        with self.session() as session:
            silence=[x for x, in session.query(Phone.mapping_id).filter(Phone.phone_type==PhoneType.silence,Phone.phone!='<eps>')]
            options=dict(self.align_options)
            boost=options.pop('boost_silence',1.0)
            aligner=mp.GmmAligner(self.alignment_model_path,silence_phones=silence,**options)
            aligner.boost_silence(boost)
            for job in session.query(Job).order_by(Job.id):
                for d in job.training_dictionaries:
                    features=job.construct_feature_archive(self.working_directory,d.name)
                    graphs=mp.FstArchive(job.construct_path(self.working_directory,'fsts','ark',d.name))
                    assert not job.construct_path(job.corpus.current_subset_directory,'ref_phones','ark',d.name).exists()
                    normal=[job.construct_path(self.working_directory,k,'ark',d.name) for k in ('ali','words','likelihoods')]
                    for p in normal:p.unlink(missing_ok=True)
                    first=aligner.acoustic_model_path.endswith('.alimdl')
                    paths=[job.construct_path(self.working_directory,k+'_first_pass','ark',d.name) for k in ('ali','words','likelihoods')] if first else normal
                    query=(session.query(Utterance.kaldi_id,Utterance.boost_silence).join(Utterance.speaker)
                           .filter(Utterance.job_id==job.id,Speaker.dictionary_id==d.id,Utterance.ignored==False,Utterance.boost_silence!=None)
                           .order_by(Utterance.kaldi_id))
                    params={u:{'boost_silence':b} for u,b in query}
                    aligner.export_alignments(paths[0],graphs,features,reference_phone_archive=None,
                                              word_file_name=paths[1],likelihood_file_name=paths[2],
                                              callback=results.append,utterance_parameters=params)
                    if first:
                        for p,target in zip(normal,paths):p.symlink_to(target)
        updates=[];total=0;count=0
        for utterance,likelihood in results:
            if likelihood:total+=likelihood;count+=1
            updates.append({'id':int(utterance.split('-')[-1]),'alignment_log_likelihood':likelihood})
        if not updates or not count:raise mix.NoAlignmentsError(self.num_current_utterances,self.beam,self.retry_beam)
        with self.session() as session:
            mix.bulk_update(session,Utterance,updates);session.commit()
            session.query(Utterance).filter(Utterance.alignment_log_likelihood!=None).update(
                {Utterance.alignment_log_likelihood:Utterance.alignment_log_likelihood/Utterance.num_frames},
                synchronize_session='fetch')
            workflow=session.query(CorpusWorkflow).filter(CorpusWorkflow.current==True).first()
            workflow.time_stamp=datetime.datetime.now();workflow.score=total/count
            session.commit()
