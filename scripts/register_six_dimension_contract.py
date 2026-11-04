#!/usr/bin/env python3
"""Explicit source registration of a built six-dimension release; no activation."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def register(schema, identity, six_schema):
    definitions = schema['$defs']
    values = {key: identity[key] for key in
              ('parentManifestSha256', 'llmManifestSha256', 'llmDependencyLockSha256', 'promptSha256')}
    for name in ('association', 'audioAssociation', 'sourceIdentity'):
        if name not in definitions:
            continue
        node = definitions[name]
        for key, value in values.items():
            if key in node['properties']:
                choices = node['properties'][key]['enum']
                if value not in choices:
                    choices.append(value)
        clause = {'properties': {key: {'const': value} for key, value in values.items()
                                 if key in node['properties']}}
        if clause not in node['oneOf']:
            node['oneOf'].append(clause)
    core_choices = definitions['binding']['properties']['parent_manifest_sha256']['enum']
    if identity['parentManifestSha256'] not in core_choices:
        core_choices.append(identity['parentManifestSha256'])

    embedded = copy.deepcopy(six_schema)
    embedded.pop('$schema', None)
    embedded.pop('$id', None)
    extra = embedded.pop('$defs')
    def rewrite(node):
        if isinstance(node, dict):
            if node.get('$ref') == '#/$defs/criterion':
                node['$ref'] = '#/$defs/sixDimensionCriterion'
            for child in node.values(): rewrite(child)
        elif isinstance(node, list):
            for child in node: rewrite(child)
    rewrite(embedded)
    definitions['sixDimensionCriterion'] = extra['criterion']
    definitions['sixDimensionResult'] = embedded
    branch = dict(type='object', additionalProperties=False, required=[
        'schemaVersion','coreInputValidation','coreSha256','adapterStatus','generationStatus',
        'items','dispatchAttempts','feedback','sixDimension',
        'naturalLanguageSemanticsFullyVerified','visualCorrectiveClaimsAllowed'], properties={
        'schemaVersion': {'const':'canonical-llm-expression-result-v1'},
        'coreInputValidation': {'$ref':'#/$defs/inputValidation'}, 'coreSha256':{'$ref':'#/$defs/sha256'},
        'adapterStatus':{'const':'READY'},
        'generationStatus':{'enum':['NOT_REQUESTED','NOT_DISPATCHED','FAILED','STRUCTURALLY_VALID']},
        'items':{'type':'array','maxItems':0}, 'dispatchAttempts':{'type':'integer','minimum':0,'maximum':1},
        'feedback':{'type':'string','minLength':1,'maxLength':4000},
        'sixDimension':{'$ref':'#/$defs/sixDimensionResult'},
        'naturalLanguageSemanticsFullyVerified':{'const':False},'visualCorrectiveClaimsAllowed':{'const':False}})
    branches = definitions['coaching']['oneOf']
    branches[:] = [b for b in branches if 'sixDimension' not in b.get('properties', {})]
    branches.append(branch)
    for name in ('result','audioResult'):
        if name not in definitions: continue
        clauses = definitions[name].get('allOf', [])
        # Ready text-only coaching has no legacy candidate projection.
        for clause in clauses:
            condition = clause.get('if', {}).get('properties', {})
            if clause.get('then', {}).get('properties', {}).get('selection') == {'const':None}:
                choices = condition.get('sourceIdentity', {}).get('properties', {}).get('llmManifestSha256', {}).get('enum')
                if choices is not None and identity['llmManifestSha256'] not in choices:
                    choices.append(identity['llmManifestSha256'])
        marker = 'six-dimension-identity-binding'
        clauses[:] = [c for c in clauses if c.get('$comment') != marker]
        clauses.append({'$comment':marker, 'if':{'properties':{'sourceIdentity':{'properties':{
            'llmManifestSha256':{'const':identity['llmManifestSha256']}}},
            'coaching':{'type':'object','properties':{'adapterStatus':{'const':'READY'}}}}},
            'then':{'properties':{'coaching':{'required':['sixDimension']}}},
            'else':{'properties':{'coaching':{'not':{'type':'object','required':['sixDimension']}}}}})
    return schema


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--h5-root',type=Path,required=True)
    args=parser.parse_args()
    raw=(args.h5_root/'contracts/llm/llm_contract_manifest.json').read_bytes()
    manifest=json.loads(raw)
    identity=dict(parentManifestSha256=manifest['parent_core_manifest_sha256'],
                  llmManifestSha256=hashlib.sha256(raw).hexdigest(),
                  llmDependencyLockSha256=manifest['dependency_lock_sha256'],promptSha256=manifest['prompt_sha256'])
    six=json.loads((ROOT/'docs/contracts/six_dimension_result_v1.schema.json').read_text())
    for name in ('runpod_result_v4.schema.json','runpod_result_v6.schema.json'):
        path=ROOT/'docs/contracts'/name
        schema=register(json.loads(path.read_text()),identity,six)
        path.write_text(json.dumps(schema,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(identity))


if __name__=='__main__': main()
