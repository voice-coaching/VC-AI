"""Explicit provisional serving of a pinned AI-Hub geometry research model.

Never relabel sentence holdout as speaker-disjoint calibration. Runtime scores
use the same measured-phase comparison as the population path, with zero
calibration/test speakers when the artifact has no such validation cohort.
"""
from dataclasses import dataclass

from .corpus_reference import CorpusPolicy, CorpusReference, digest, require
from .corpus_research_model import SCHEMA
from .feature_protocol import MAPPING, finite
from .lip_scoring import BILABIALS, VOWELS, LABELS

ASSESSMENT = 'AIHUB_RESEARCH_30FPS_V1'


@dataclass(frozen=True)
class ResearchReference:
    value: dict

    @property
    def policy(self):
        return CorpusPolicy()

    @classmethod
    def load(cls, value):
        require(type(value) is dict and value.get('schemaVersion') == SCHEMA)
        require(value.get('datasetId') == '538' and value.get('modelType') == 'PHONEME_PHASE_GAUSSIAN_GEOMETRY'
                and value.get('validationStatus') == 'RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION'
                and value.get('scoreMeaning') == 'REFERENCE_PROXIMITY_NOT_PRONUNCIATION_ACCURACY')
        require(value.get('mappingRevision') == MAPPING.revision
                and all(digest(value.get(k)) for k in ('modelSha256', 'qualitySha256')))
        require(value.get('scoreFormula') == '100*exp(-0.5*((x-mean)/standardDeviation)**2)'
                and value.get('heldOutSentenceSplitIsSpeakerIndependent') is False)
        for k in ('sourceSpeakerCount', 'sourceSentenceCount', 'trainSentenceCount', 'heldOutSentenceCount', 'minimumObservations'):
            require(type(value.get(k)) is int and value[k] > 0)
        require(value['sourceSpeakerCount'] >= 3 and value['minimumObservations'] >= 5
                and value['sourceSentenceCount'] == value['trainSentenceCount'] + value['heldOutSentenceCount'])
        require(type(value.get('sourceVideoSha256s')) is list and bool(value['sourceVideoSha256s'])
                and all(digest(s) for s in value['sourceVideoSha256s']))
        require(type(value.get('datasetManifestSha256s')) is list and bool(value['datasetManifestSha256s'])
                and all(digest(s) for s in value['datasetManifestSha256s']))
        require(type(value.get('statistics')) is list and 0 < len(value['statistics']) <= 10000)
        seen = set()
        for r in value['statistics']:
            key = tuple(r[k] for k in ('phone', 'role', 'criterionId', 'phaseBin', 'contextId'))
            require(key not in seen); seen.add(key)
            phone, role, metric, phase, context = key
            require(metric in LABELS and ((phone in VOWELS and role == 'nucleus')
                    or (phone in BILABIALS and role in {'onset', 'coda'})))
            require((metric in {'lip_aperture', 'lip_rounding'} and phase in {'0','1','2','3'}
                     and context == 'ANY' and role == 'nucleus' and (metric != 'lip_rounding' or phone in 'ㅗㅜㅛㅠ'))
                    or (metric == 'lip_closure' and phase == 'WHOLE' and context == 'ANY' and phone in BILABIALS)
                    or (metric == 'lip_timing' and phase == 'WHOLE' and digest(context)))
            require(finite(r['mean']) and 0 <= r['mean'] <= (1 if metric == 'lip_timing' else 100)
                    and finite(r['standardDeviation']) and r['standardDeviation'] > 0)
            require(type(r['trainSpeakerCount']) is int and 1 <= r['trainSpeakerCount'] <= value['sourceSpeakerCount']
                    and type(r['trainOccurrenceCount']) is int and r['trainOccurrenceCount'] >= value['minimumObservations'])
        return cls(value)

    assess = CorpusReference.assess
