"""Learned phoneme/phase geometry model for feasibility tests, NOT production.

Unlike the population candidate this explicitly allows a single speaker and
holds out whole sentences. Its artifact cannot be loaded as a runtime catalog.
It measures reference proximity, never expert correctness or normality.
"""
from collections import defaultdict
import math
import statistics
from types import SimpleNamespace

from .corpus_reference import (CorpusPolicy,encode,measure_phone,neighbour_context,require,sha)
from .feature_protocol import MAPPING
from .audiovisual_evidence import LipFrame
from .lip_scoring import display,LABELS,BILABIALS,VOWELS

SCHEMA='voice-coaching.aihub538-phoneme-geometry-research-model.v1'


def fit(records,*,minimum_observations=5):
    require(type(minimum_observations) is int and minimum_observations>=2)
    require(bool(records),'CORPUS_RESEARCH_TRAINING_DATA_REQUIRED')
    model_sha={r['modelSha256'] for r in records};quality_sha={r['qualitySha256'] for r in records}
    manifest={r['datasetManifestSha256'] for r in records}
    require(len(model_sha)==len(quality_sha)==1 and bool(manifest))
    groups=defaultdict(lambda:defaultdict(list));seen=set();speakers=set();entries=set();sources=set()
    for row in records:
        require(row['datasetId']=='538' and row['speakerType']=='EXPERT' and row['view']=='A'
            and row['noise']=='1' and row['sourcePartition']=='TRAIN' and row['mappingRevision']==MAPPING.revision)
        speaker=row['speakerSha256'];entry=row['entryId'];speakers.add(speaker);entries.add(entry)
        sources.update((row['sourceVideoSha256'],row['normalizedVideoSha256']))
        split='TEST' if int(entry[:8],16)%5==0 else 'TRAIN'
        for m in row['metrics']:
            key=(row['phone'],row['role'],m['criterionId'],m['phaseBin'],m['contextId'])
            identity=(row['occurrenceId'],key);require(identity not in seen,'CORPUS_DUPLICATE_OCCURRENCE');seen.add(identity)
            groups[key][split].append((speaker,m['value']))
    learned=[]
    for key,parts in sorted(groups.items()):
        train=parts['TRAIN'];test=parts['TEST']
        if len(train)<minimum_observations:continue
        by_speaker=defaultdict(list)
        for speaker,value in train:by_speaker[speaker].append(value)
        means=[statistics.mean(v) for v in by_speaker.values()]
        mean=statistics.mean(means)
        variance=(statistics.variance(means) if len(means)>1 else 0)+statistics.mean(
            statistics.variance(v) if len(v)>1 else 0 for v in by_speaker.values())
        if variance<=0 or not math.isfinite(variance):continue
        sd=math.sqrt(variance);z=[(v-mean)/sd for _,v in test]
        learned.append(dict(phone=key[0],role=key[1],criterionId=key[2],phaseBin=key[3],contextId=key[4],
            mean=mean,standardDeviation=sd,trainOccurrenceCount=len(train),trainSpeakerCount=len(by_speaker),
            heldOutSentenceObservationCount=len(test),heldOutMeanAbsoluteZ=statistics.mean(map(abs,z)) if z else None))
    require(bool(learned),'CORPUS_RESEARCH_MINIMUM_OBSERVATIONS')
    train_entries={r['entryId'] for r in records if int(r['entryId'][:8],16)%5!=0}
    test_entries=entries-train_entries
    return dict(schemaVersion=SCHEMA,modelType='PHONEME_PHASE_GAUSSIAN_GEOMETRY',
        validationStatus='RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION',
        scoreMeaning='REFERENCE_PROXIMITY_NOT_PRONUNCIATION_ACCURACY',datasetId='538',
        datasetManifestSha256=next(iter(manifest)) if len(manifest)==1 else None,
        datasetManifestSha256s=sorted(manifest),modelSha256=next(iter(model_sha)),qualitySha256=next(iter(quality_sha)),
        mappingRevision=MAPPING.revision,sourceSpeakerCount=len(speakers),sourceSentenceCount=len(entries),
        trainSentenceCount=len(train_entries),heldOutSentenceCount=len(test_entries),
        heldOutSentenceSplitIsSpeakerIndependent=False,sourceVideoSha256s=sorted(sources),minimumObservations=minimum_observations,
        scoreFormula='100*exp(-0.5*((x-mean)/standardDeviation)**2)',statistics=learned)


def evaluate(model,evidence):
    require(model['schemaVersion']==SCHEMA and model['validationStatus']=='RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION')
    require(evidence['decoder']['modelSha256']==model['modelSha256'] and evidence['mappingRevision']==model['mappingRevision'],
            'CORPUS_FEATURE_PROTOCOL_MISMATCH')
    require(evidence['videoSha256'] not in model['sourceVideoSha256s'],'REFERENCE_SELF_COMPARISON')
    refs={tuple(r[k] for k in ('phone','role','criterionId','phaseBin','contextId')):r for r in model['statistics']}
    frames=tuple(LipFrame(r['pts'],tuple(map(tuple,r['landmarks'])),tuple(r['features']),r['reason'] is None)
                 for r in evidence['frames'])
    alignment=evidence.get('alignmentSidecar',{}).get('alignment',{}).get('rows',[])
    eligible=lambda phone,role:(role=='nucleus' and phone in VOWELS) or (role in {'onset','coda'} and phone in BILABIALS)
    eligible_count=sum(eligible(r['expected_phone'],r['role']) for r in alignment) if alignment else None
    phone_results=[];component_scores=defaultdict(list);observed_eligible=measurable=0
    for row in evidence['visual']['phoneAssessments']:
        if (row['videoStartSeconds'] is None or row['frameCount']<1 or 'MULTIPLE_FACES' in row['reasonCodes']):continue
        anchor=SimpleNamespace(expected_phone=row['expectedPhone'],role=row['role'])
        if eligible(anchor.expected_phone,anchor.role):observed_eligible+=1
        metrics,_=measure_phone(anchor,frames,start=row['videoStartSeconds'],end=row['videoEndSeconds'],
            context=neighbour_context(alignment,row['expectedIndex']),policy=CorpusPolicy())
        if metrics:measurable+=1
        comparisons=[];by_component=defaultdict(list)
        for (metric,phase,context),value in metrics.items():
            ref=refs.get((anchor.expected_phone,anchor.role,metric,phase,context))
            if ref is None:continue
            z=(value-ref['mean'])/ref['standardDeviation'];score=100*math.exp(-.5*z*z)
            comparisons.append(dict(criterionId=metric,phaseBin=phase,value=value,mean=ref['mean'],
                standardDeviation=ref['standardDeviation'],z=z,score=display(score),
                trainOccurrenceCount=ref['trainOccurrenceCount'],trainSpeakerCount=ref['trainSpeakerCount']))
            by_component[metric].append(score)
        if comparisons:
            subs={metric:statistics.mean(values) for metric,values in by_component.items()}
            for metric,value in subs.items():component_scores[metric].append(value)
            phone_results.append(dict(expectedIndex=row['expectedIndex'],expectedPhone=row['expectedPhone'],role=row['role'],
                score=display(statistics.mean(subs.values())),comparisons=comparisons))
    return dict(schemaVersion='voice-coaching.aihub538-research-evaluation.v1',modelSha256=sha(encode(model)),
        sourceSpeakerCount=model['sourceSpeakerCount'],scoreMeaning=model['scoreMeaning'],
        validationStatus=model['validationStatus'],testedVideoSha256=evidence['videoSha256'],
        expectedPhoneCount=evidence['visual']['coverage']['targetCount'],scoredPhoneCount=len(phone_results),
        visuallyEligiblePhoneCount=eligible_count,
        observedEligiblePhoneCount=observed_eligible,measurablePhoneCount=measurable,
        missingReferencePhoneCount=measurable-len(phone_results),
        scoredCoverage=len(phone_results)/eligible_count if eligible_count else None,
        score=display(statistics.mean(p['score'] for p in phone_results)) if phone_results else None,
        subScores={key:display(statistics.mean(component_scores[key])) if component_scores[key] else None for key in LABELS},
        phoneResults=phone_results,providerPosts=0)


def cross_speaker_diagnostics(records,*,minimum_observations=5):
    """Leave one speaker out. This is distribution testing, not error accuracy."""
    speakers=sorted({r['speakerSha256'] for r in records})
    if len(speakers)<3:
        return dict(status='INSUFFICIENT_SPEAKERS',sourceSpeakerCount=len(speakers),folds=[],
            meaning='DISTRIBUTION_GENERALIZATION_NOT_PRONUNCIATION_ACCURACY')
    folds=[]
    for index,held in enumerate(speakers):
        train=[r for r in records if r['speakerSha256']!=held]
        test=[r for r in records if r['speakerSha256']==held]
        try:model=fit(train,minimum_observations=minimum_observations)
        except ValueError:
            folds.append(dict(foldIndex=index,status='INSUFFICIENT_REFERENCE_DATA'));continue
        refs={tuple(r[k] for k in ('phone','role','criterionId','phaseBin','contextId')):r for r in model['statistics']}
        by_component=defaultdict(list);possible=0
        for row in test:
            for metric in row['metrics']:
                possible+=1
                ref=refs.get((row['phone'],row['role'],metric['criterionId'],metric['phaseBin'],metric['contextId']))
                if ref is not None:by_component[metric['criterionId']].append(abs((metric['value']-ref['mean'])/ref['standardDeviation']))
        folds.append(dict(foldIndex=index,status='EVALUATED',trainSpeakerCount=len(speakers)-1,
            heldOutSpeakerCount=1,trainSpeakerSha256s=[s for s in speakers if s!=held],
            heldOutSpeakerSha256=held,possibleObservationCount=possible,
            comparedObservationCount=sum(map(len,by_component.values())),
            components={key:dict(count=len(values),meanAbsoluteZ=statistics.mean(values),
                fractionWithinTwoSD=sum(v<=2 for v in values)/len(values)) for key,values in by_component.items()}))
    return dict(status='EVALUATED' if any(f['status']=='EVALUATED' for f in folds) else 'INSUFFICIENT_REFERENCE_DATA',
        sourceSpeakerCount=len(speakers),folds=folds,
        meaning='DISTRIBUTION_GENERALIZATION_NOT_PRONUNCIATION_ACCURACY')
