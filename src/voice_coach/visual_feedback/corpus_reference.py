"""AI-Hub population proximity, not pronunciation truth or a normality probability.

One phone occurrence contributes once per phase bin. Speakers have equal weight;
within-speaker variability is retained in the predictive variance. Whole-motion
statistics require matching neighbouring phones. Raw media/transcripts stay out.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import math
import statistics
import json

from .feature_protocol import MAPPING, finite
from .lip_scoring import BILABIALS, VOWELS, LABELS, display

SCHEMA = 'voice-coaching.aihub-lip-population.v1'
MEASUREMENTS = 'voice-coaching.aihub-lip-measurements.v1'
ASSESSMENT = 'AIHUB_POPULATION_30FPS_V1'
REVISION = 'aihub538-speaker-balanced-phase-v1'


def require(ok, code='CORPUS_REFERENCE_INVALID'):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))+'\n').encode()


def digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def speaker_split(speaker_sha256):
    require(digest(speaker_sha256))
    bucket = int(speaker_sha256[:8], 16) % 10
    return 'TRAIN' if bucket < 7 else 'CALIBRATION' if bucket < 9 else 'TEST'


@dataclass(frozen=True)
class CorpusPolicy:
    revision: str = REVISION
    minimum_train_speakers: int = 10
    minimum_train_occurrences: int = 30
    minimum_calibration_speakers: int = 3
    minimum_test_speakers: int = 3
    phase_bins: int = 4
    minimum_motion: float = 0.01
    decay: float = 0.5

    def validate(self):
        require(self.revision == REVISION)
        for key in ('minimum_train_speakers','minimum_train_occurrences',
                    'minimum_calibration_speakers','minimum_test_speakers'):
            require(type(getattr(self,key)) is int and 1 <= getattr(self,key) <= 100000)
        require(self.minimum_train_speakers >= 3 and self.minimum_train_occurrences >= self.minimum_train_speakers)
        require(self.phase_bins == 4 and finite(self.minimum_motion) and self.minimum_motion > 0
                and finite(self.decay) and self.decay > 0)


def neighbour_context(rows, index):
    by_index = {r['expected_index']: r for r in rows}
    return sha(('|'.join(by_index[i]['expected_phone'] if i in by_index else '#'
                         for i in (index-1,index,index+1))).encode())


def measure_phone(anchor, frames, *, start, end, context, policy):
    """Same actual-frame protocol for corpus construction and user scoring."""
    selected = [f for f in frames if start <= f.pts_s < end]
    visible = [f for f in selected if f.observable]
    if not visible:
        return {}, False
    require(0 < end-start and digest(context))
    partial = len(visible) != len(selected)
    result = {}
    vowel = anchor.role == 'nucleus' and anchor.expected_phone in VOWELS
    bilabial = anchor.role in {'onset','coda'} and anchor.expected_phone in BILABIALS
    for metric,feature_index in [('lip_aperture',0),('lip_rounding',1)]:
        if not vowel or metric == 'lip_rounding' and anchor.expected_phone not in 'ㅗㅜㅛㅠ':
            continue
        bins = defaultdict(list)
        for f in visible:
            phase = min(policy.phase_bins-1, int((f.pts_s-start)/(end-start)*policy.phase_bins))
            bins[str(phase)].append(f.features[feature_index])
        result.update({(metric,phase,'ANY'): statistics.median(values) for phase,values in bins.items()})
    if not partial and bilabial and len(visible) >= 2:
        result[('lip_closure','WHOLE','ANY')] = min(f.features[0] for f in visible)
    if not partial and (vowel or bilabial) and len(visible) >= 3:
        aperture = [f.features[0] for f in visible]
        if max(aperture)-min(aperture) >= policy.minimum_motion:
            event = min(aperture) if bilabial else max(aperture)
            pts = statistics.median(f.pts_s for f in visible if f.features[0] == event)
            result[('lip_timing','WHOLE',context)] = (pts-start)/(end-start)
    return result, partial


def build_population(records, *, dataset_manifest_sha256, model_sha256, quality_sha256,
                     revision, policy=None):
    """Fit TRAIN speakers; independently summarize CALIBRATION and TEST speakers.

    Input must be produced from hash-verified AI-Hub 538 TRAIN source media.
    Missing groups remain absent. A single exemplar can never become a catalog.
    """
    policy = policy or CorpusPolicy();policy.validate()
    require(all(digest(x) for x in (dataset_manifest_sha256,model_sha256,quality_sha256)))
    groups = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    sources=set();occurrences=set();speakers=set();seen=set()
    for row in records:
        require(row.get('schemaVersion') == MEASUREMENTS and row.get('datasetId') == '538'
                and row.get('sourcePartition') == 'TRAIN' and row.get('speakerType') == 'EXPERT'
                and row.get('view') == 'A' and row.get('noise') == '1', 'CORPUS_SOURCE_COHORT')
        require(row.get('datasetManifestSha256') == dataset_manifest_sha256
                and row.get('mappingRevision') == MAPPING.revision
                and row.get('modelSha256') == model_sha256 and row.get('qualitySha256') == quality_sha256,
                'CORPUS_FEATURE_PROTOCOL_MISMATCH')
        require(all(digest(row.get(k)) for k in ('speakerSha256','sourceVideoSha256','normalizedVideoSha256','occurrenceId','coreSha256','alignmentSha256')))
        require(row['phone'] in VOWELS and row['role']=='nucleus'
                or row['phone'] in BILABIALS and row['role'] in {'onset','coda'})
        speaker=row['speakerSha256'];split=speaker_split(speaker)
        sources.update((row['sourceVideoSha256'],row['normalizedVideoSha256']))
        speakers.add(speaker);occurrences.add(row['occurrenceId'])
        require(type(row['metrics']) is list)
        for m in row['metrics']:
            require(set(m)=={'criterionId','phaseBin','contextId','value'})
            metric,phase,context=m['criterionId'],m['phaseBin'],m['contextId']
            require(metric in LABELS and finite(m['value']) and 0 <= m['value'] <= 100)
            require(phase in {'0','1','2','3','WHOLE'} and (context=='ANY' or digest(context)))
            require((metric in {'lip_aperture','lip_rounding'} and phase!='WHOLE' and context=='ANY')
                    or (metric=='lip_closure' and phase=='WHOLE' and context=='ANY')
                    or (metric=='lip_timing' and phase=='WHOLE' and digest(context) and m['value'] <= 1))
            require(metric not in {'lip_aperture','lip_rounding'} or row['role']=='nucleus')
            require(metric!='lip_rounding' or row['phone'] in 'ㅗㅜㅛㅠ')
            require(metric!='lip_closure' or row['phone'] in BILABIALS)
            key=(row['phone'],row['role'],metric,phase,context)
            identity=(row['sourceVideoSha256'],row['occurrenceId'],*key)
            require(identity not in seen,'CORPUS_DUPLICATE_OCCURRENCE');seen.add(identity)
            groups[key][split][speaker].append(m['value'])
    statistics_rows=[];missing=[]
    for key,partitions in sorted(groups.items()):
        train=partitions['TRAIN'];calibration=partitions['CALIBRATION'];test=partitions['TEST']
        counts=dict(trainSpeakerCount=len(train),trainOccurrenceCount=sum(map(len,train.values())),
                    calibrationSpeakerCount=len(calibration),testSpeakerCount=len(test))
        if (len(train)<policy.minimum_train_speakers or counts['trainOccurrenceCount']<policy.minimum_train_occurrences
                or len(calibration)<policy.minimum_calibration_speakers or len(test)<policy.minimum_test_speakers):
            missing.append(dict(phone=key[0],role=key[1],criterionId=key[2],phaseBin=key[3],reason='INSUFFICIENT_SPEAKERS',**counts));continue
        means=[statistics.mean(v) for v in train.values()]
        mean=statistics.mean(means)
        between=statistics.variance(means)
        within=statistics.mean(statistics.variance(v) if len(v)>1 else 0.0 for v in train.values())
        scale=math.sqrt(between+within)
        if not math.isfinite(scale) or scale<=0:
            missing.append(dict(phone=key[0],role=key[1],criterionId=key[2],phaseBin=key[3],reason='ZERO_REFERENCE_VARIANCE',**counts));continue
        def holdout(values):
            z=[abs(statistics.mean(v)-mean)/scale for v in values.values()]
            return dict(speakerCount=len(z),meanAbsoluteZ=statistics.mean(z),fractionWithinTwoSD=sum(x<=2 for x in z)/len(z))
        statistics_rows.append(dict(phone=key[0],role=key[1],criterionId=key[2],phaseBin=key[3],contextId=key[4],
            mean=mean,standardDeviation=scale,betweenSpeakerVariance=between,withinSpeakerVariance=within,
            **counts,calibration=holdout(calibration),test=holdout(test)))
    return dict(schemaVersion=SCHEMA,revision=revision,datasetId='538',
        datasetManifestSha256=dataset_manifest_sha256,mappingRevision=MAPPING.revision,
        modelSha256=model_sha256,qualitySha256=quality_sha256,policy=asdict(policy),
        sourceVideoSha256s=sorted(sources),sourceSpeakerCount=len(speakers),sourceOccurrenceCount=len(occurrences),
        validationStatus='PASSED' if statistics_rows else 'INSUFFICIENT_REFERENCE_DATA',
        validationMeaning='SPEAKER_DISJOINT_DISTRIBUTION_CHECKS_ONLY',
        scoreMeaning='CORPUS_REFERENCE_PROXIMITY_NOT_EXPERT_ACCURACY',
        statistics=statistics_rows,unavailableGroups=missing)


@dataclass(frozen=True)
class CorpusReference:
    value: dict

    @property
    def policy(self):
        return CorpusPolicy(**self.value['policy'])

    @classmethod
    def load(cls,value):
        require(type(value) is dict and value.get('schemaVersion')==SCHEMA and value.get('datasetId')=='538')
        policy=CorpusPolicy(**value['policy']);policy.validate()
        require(value.get('validationStatus')=='PASSED' and bool(value.get('statistics')),
                'CORPUS_REFERENCE_DATA_REQUIRED')
        require(value.get('mappingRevision')==MAPPING.revision and
                all(digest(value.get(k)) for k in ('datasetManifestSha256','modelSha256','qualitySha256')))
        require(value.get('validationMeaning')=='SPEAKER_DISJOINT_DISTRIBUTION_CHECKS_ONLY'
                and value.get('scoreMeaning')=='CORPUS_REFERENCE_PROXIMITY_NOT_EXPERT_ACCURACY')
        require(type(value.get('sourceVideoSha256s')) is list and bool(value['sourceVideoSha256s'])
                and all(digest(s) for s in value['sourceVideoSha256s']))
        seen=set()
        for r in value['statistics']:
            key=tuple(r[k] for k in ('phone','role','criterionId','phaseBin','contextId'))
            require(key not in seen);seen.add(key)
            require(r['criterionId'] in LABELS and r['phaseBin'] in {'0','1','2','3','WHOLE'})
            require((r['role']=='nucleus' and r['phone'] in VOWELS) or
                    (r['role'] in {'onset','coda'} and r['phone'] in BILABIALS))
            metric,phase,context=r['criterionId'],r['phaseBin'],r['contextId']
            require((metric in {'lip_aperture','lip_rounding'} and phase!='WHOLE' and context=='ANY'
                     and r['role']=='nucleus' and (metric!='lip_rounding' or r['phone'] in 'ㅗㅜㅛㅠ'))
                    or (metric=='lip_closure' and phase=='WHOLE' and context=='ANY' and r['phone'] in BILABIALS)
                    or (metric=='lip_timing' and phase=='WHOLE' and digest(context)))
            require(finite(r['mean']) and 0 <= r['mean'] <= 100 and finite(r['standardDeviation']) and r['standardDeviation']>0)
            require(metric!='lip_timing' or r['mean']<=1)
            require(all(type(r[k]) is int for k in ('trainSpeakerCount','trainOccurrenceCount',
                    'calibrationSpeakerCount','testSpeakerCount')))
            require(r['trainSpeakerCount']>=policy.minimum_train_speakers and
                    r['trainOccurrenceCount']>=policy.minimum_train_occurrences and
                    r['calibrationSpeakerCount']>=policy.minimum_calibration_speakers and
                    r['testSpeakerCount']>=policy.minimum_test_speakers)
            require(all(finite(r[k]) and r[k]>=0 for k in ('betweenSpeakerVariance','withinSpeakerVariance')))
            require(math.isclose(r['standardDeviation']**2,r['betweenSpeakerVariance']+r['withinSpeakerVariance'],rel_tol=1e-10))
            require(r['calibration']['speakerCount']==r['calibrationSpeakerCount'] and r['test']['speakerCount']==r['testSpeakerCount'])
            for split in ('calibration','test'):
                require(finite(r[split]['meanAbsoluteZ']) and r[split]['meanAbsoluteZ']>=0
                        and finite(r[split]['fractionWithinTwoSD']) and 0<=r[split]['fractionWithinTwoSD']<=1)
        return cls(value)

    def assess(self,row,anchor,frames,*,context,video_sha256,source_sha256):
        if row['videoStartSeconds'] is None or row['frameCount']<1 or row['status'] not in {'OBSERVED','REFERENCE_ONLY','SCORED'}:
            return []
        if video_sha256 in self.value['sourceVideoSha256s'] or source_sha256 in self.value['sourceVideoSha256s']:
            row.update(score=None,status='OBSERVED',scoreKind='NONE',reasonCodes=['REFERENCE_SELF_COMPARISON']);return []
        measured,partial=measure_phone(anchor,frames,start=row['videoStartSeconds'],end=row['videoEndSeconds'],
                                      context=context,policy=self.policy)
        refs={tuple(r[k] for k in ('phone','role','criterionId','phaseBin','contextId')):r for r in self.value['statistics']}
        comparisons=[];scores=defaultdict(list)
        for (metric,phase,ctx),observed in sorted(measured.items()):
            ref=refs.get((anchor.expected_phone,anchor.role,metric,phase,ctx))
            if ref is None:continue
            z=(observed-ref['mean'])/ref['standardDeviation']
            score=100*math.exp(-self.policy.decay*z*z)
            scores[metric].append(score)
            comparisons.append(dict(criterionId=metric,phaseBin=phase,contextId=ctx,value=observed,
                referenceMean=ref['mean'],referenceStandardDeviation=ref['standardDeviation'],zScore=z,
                direction='LOWER' if z<0 else 'HIGHER' if z>0 else 'AT_MEAN',
                trainSpeakerCount=ref['trainSpeakerCount'],trainOccurrenceCount=ref['trainOccurrenceCount'],
                calibrationSpeakerCount=ref.get('calibrationSpeakerCount',0),testSpeakerCount=ref.get('testSpeakerCount',0)))
        row['subScores']=[dict(criterionId=key,label=label,
            score=display(statistics.mean(scores[key])) if scores[key] else None,
            status='SCORED' if scores[key] else 'UNSCORABLE',
            reasonCode='AIHUB_CORPUS_REFERENCE_PROXIMITY' if scores[key] else 'CORPUS_COMPONENT_REFERENCE_UNAVAILABLE',
            measurements=[]) for key,label in LABELS.items()]
        # Display each component independently; do not round it before the
        # phone mean, which must match the offline learned-model evaluator.
        available=[statistics.mean(values) for values in scores.values() if values]
        row['reasonCodes']=['AIHUB_CORPUS_REFERENCE_PROXIMITY' if available else 'CORPUS_REFERENCE_UNAVAILABLE']
        if partial:row['reasonCodes'].append('PARTIAL_VISIBILITY_STATIC_ONLY')
        if available:
            row.update(status='SCORED',score=display(statistics.mean(available)),scoreKind='PROVISIONAL',
                       allowedActionIds=[],referenceIds=[],referenceDistance=None,calibrationRevision=None)
        return comparisons
