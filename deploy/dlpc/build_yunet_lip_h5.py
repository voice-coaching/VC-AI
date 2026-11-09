"""Bind re-measured AI-Hub references and YuNet to immutable H5s; no inference."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'deploy/runpod'));sys.path.insert(0,str(ROOT/'src'))
from release_packaging import encode,load_files,sha,write_release
from build_aihub_lip_h5 import research_catalog
from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
from voice_coach.visual_feedback.adapters.yunet_landmarks import read_bundle,REVISION
from voice_coach.visual_feedback.corpus_reference import encode as feature_encode

PARENTS={'direct':'a827304fca571741f72e2b660bbeececb3ec153778024a0c409e28049329d2cd',
         'internal':'5ba067af6f3300994a2b5d04e425951419cd3ff30029a2b5e42897f11b47aaff'}


def build(source,destination,reference_model,model_bundle,lane):
    raw=(source/'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw)!=PARENTS[lane]:raise ValueError('YUNET_H5_PARENT_MISMATCH')
    parent=json.loads(raw)
    _,inherited=load_files(source,'contracts/llm/dependency_lock.json',parent['dependency_lock_sha256'],'files')
    files=dict(inherited)
    bundle=model_bundle.read_bytes()
    if read_bundle(bundle) is None:raise ValueError('YUNET_BUNDLE_REQUIRED')
    catalog_raw=research_catalog(reference_model,revision='aihub538-yunet-20261109-v1')
    catalog=ReferenceCatalog.from_bytes(catalog_raw,sha(catalog_raw))
    config=json.loads(files['contracts/visual/runtime-candidate.json'])
    config['modelSha256']=sha(bundle)
    if (catalog.corpus.value['modelSha256']!=sha(bundle)
            or catalog.corpus.value['qualitySha256']!=sha(feature_encode(config['quality']))):
        raise ValueError('YUNET_REFERENCE_EXTRACTOR_MISMATCH')
    files['contracts/visual/reference-catalog.json']=catalog_raw
    files['contracts/visual/runtime-candidate.json']=encode(config)
    changed={name for name in files if files[name]!=inherited[name]}
    if changed!={'contracts/visual/reference-catalog.json','contracts/visual/runtime-candidate.json'}:
        raise ValueError('YUNET_H5_UNEXPECTED_CHANGE')
    directory=f'canonical_yunet_lip_{lane}_20261109_v1_h5'
    lock=encode(dict(files={name:sha(body) for name,body in sorted(files.items())}))
    manifest=encode(dict(parent,contract_version=directory,parent_llm_manifest_sha256=sha(raw),
        dependency_lock_sha256=sha(lock),visual_reference_catalog_sha256=sha(catalog_raw),
        visual_extractor_revision=REVISION,visual_model_bundle_sha256=sha(bundle),
        productionActivated=False,developerQaVerified=False))
    files['contracts/llm/dependency_lock.json']=lock
    files['contracts/llm/llm_contract_manifest.json']=manifest
    target=destination/directory;write_release(target,files)
    return dict(root=str(target),manifestSha256=sha(manifest),lockSha256=sha(lock),
        promptSha256=parent['prompt_sha256'],coreSha256=parent['parent_core_manifest_sha256'],
        modelBundleSha256=sha(bundle),referenceCatalogSha256=sha(catalog_raw),
        changedFiles=sorted(changed),providerPosts=0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','destination','reference-model','model-bundle'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--lane',choices=tuple(PARENTS),required=True)
    a=p.parse_args();print(json.dumps(build(a.source,a.destination,a.reference_model,a.model_bundle,a.lane)))
