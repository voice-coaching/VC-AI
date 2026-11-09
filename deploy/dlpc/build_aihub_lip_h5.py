"""Package population or explicitly provisional research lip models for QA."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'deploy/runpod'));sys.path.insert(0,str(ROOT/'src'))
from release_packaging import encode,load_files,sha,write_release
from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
from voice_coach.visual_feedback.corpus_reference import encode as feature_encode

PARENTS={'direct':'26aa0e405104f0bb01383b808c36ac90d61d32e310e681a4d602925fe4c73a3b',
         'internal':'232df793470dfdbcda38057f34b6c36d18dbd90f128171b24342baa3878c1b78'}
# Keep the exact existing parent pins; registration is generated only after
# real source/receipt extraction and separate end-to-end QA, not from examples.


def research_catalog(model_path, *, revision):
    from voice_coach.visual_feedback.feature_protocol import strict_json
    model_raw=model_path.read_bytes();model=strict_json(model_raw)
    if model_raw!=feature_encode(model):raise ValueError('AIHUB_MODEL_CANONICAL_BYTES_REQUIRED')
    base=json.loads((ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes())
    base.update(schemaVersion='voice-coaching.lip-reference-catalog.v6',revision=revision,
        contextPolicy='CORPUS_PHASE_AND_NEIGHBOUR_V1',minimumCoverage=.6,
        researchModel=model,researchModelSha256=sha(model_raw),references=[],calibrations=[])
    raw=feature_encode(base)
    ReferenceCatalog.from_bytes(raw,sha(raw))
    return raw


def build(source,destination,*,catalog_path=None,model_path=None,allow_provisional_research=False,lane,revision):
    parent_raw=(source/'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(parent_raw)!=PARENTS[lane]:raise ValueError('AIHUB_LIP_PARENT_MISMATCH')
    parent=json.loads(parent_raw)
    _,files=load_files(source,'contracts/llm/dependency_lock.json',parent['dependency_lock_sha256'],'files')
    if bool(catalog_path)==bool(model_path):raise ValueError('AIHUB_EXACTLY_ONE_REFERENCE_REQUIRED')
    if model_path is not None and not allow_provisional_research:
        raise ValueError('AIHUB_EXPLICIT_PROVISIONAL_MODE_REQUIRED')
    files=dict(files)
    catalog_raw=research_catalog(model_path,revision=revision) if model_path is not None else catalog_path.read_bytes()
    catalog=ReferenceCatalog.from_bytes(catalog_raw,sha(catalog_raw))
    if catalog.corpus is None:raise ValueError('AIHUB_POPULATION_REFERENCE_REQUIRED')
    research=catalog.assessment_policy=='AIHUB_RESEARCH_30FPS_V1'
    if research and not allow_provisional_research:raise ValueError('AIHUB_EXPLICIT_PROVISIONAL_MODE_REQUIRED')
    p=catalog.corpus.policy
    if not research and (p.minimum_train_speakers<10 or p.minimum_train_occurrences<30
            or p.minimum_calibration_speakers<3 or p.minimum_test_speakers<3):
        raise ValueError('AIHUB_PRODUCTION_MINIMUM_REFERENCE_REQUIRED')
    config=json.loads(files['contracts/visual/runtime-candidate.json'])
    if (catalog.corpus.value['modelSha256']!=config['modelSha256'] or
            catalog.corpus.value['qualitySha256']!=sha(feature_encode(config['quality']))):
        raise ValueError('AIHUB_FEATURE_PROTOCOL_MISMATCH')
    request=json.loads(files['contracts/llm/request_config.json'])
    if (request['model'],request['reasoning_effort'],request['service_tier'])!=('gpt-6-luna','none','default'):
        raise ValueError('AIHUB_REQUEST_MISMATCH')
    files['contracts/visual/reference-catalog.json']=catalog_raw
    files['bridge/visual.py']=(ROOT/'src/voice_coach/six_dimension/visual.py').read_bytes()
    prompt='contracts/llm/llm_system_prompt.txt'
    files[prompt]+=(ROOT/'configs/coaching/aihub_lip_supplement_v1.txt').read_bytes()
    directory='canonical_aihub_lip_'+lane+'_'+revision+'_h5'
    if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in revision) or not revision:
        raise ValueError('AIHUB_RELEASE_REVISION_INVALID')
    lock=encode(dict(files={k:sha(v) for k,v in sorted(files.items())}))
    manifest=dict(parent,contract_version=directory,parent_llm_manifest_sha256=sha(parent_raw),
        dependency_lock_sha256=sha(lock),prompt_sha256=sha(files[prompt]),
        visual_assessment_policy=catalog.assessment_policy,
        lip_reference_validation_status=catalog.corpus.value['validationStatus'],
        productionActivated=False,developerQaVerified=False)
    raw=encode(manifest)
    files['contracts/llm/dependency_lock.json']=lock;files['contracts/llm/llm_contract_manifest.json']=raw
    write_release(destination/directory,files)
    return dict(root=str(destination/directory),manifestSha256=sha(raw),lockSha256=sha(lock),promptSha256=manifest['prompt_sha256'],
        coreSha256=parent['parent_core_manifest_sha256'],catalogSha256=sha(catalog_raw),
        assessmentPolicy=catalog.assessment_policy,referenceValidationStatus=catalog.corpus.value['validationStatus'],
        runtimeRegistrationRequired=True,productionActivated=False,providerPosts=0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','destination'):p.add_argument('--'+name,type=Path,required=True)
    choice=p.add_mutually_exclusive_group(required=True)
    choice.add_argument('--catalog-path',type=Path);choice.add_argument('--research-model',type=Path)
    p.add_argument('--allow-provisional-research',action='store_true')
    p.add_argument('--lane',choices=tuple(PARENTS),required=True);p.add_argument('--revision',required=True)
    a=p.parse_args();print(json.dumps(build(a.source,a.destination,catalog_path=a.catalog_path,
        model_path=a.research_model,allow_provisional_research=a.allow_provisional_research,lane=a.lane,revision=a.revision)))
