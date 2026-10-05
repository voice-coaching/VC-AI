"""Adapt the observed TTS server with an explicit, hash-pinned DLPC budget."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import replace_once

SOURCE_SHA = '8b41fcf817af76a9c0aaabae0df7c3a8efcd03c754fe92bffe3963daa17b4282'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError('TTS_SOURCE_CHANGED')
    source = raw.decode()
    source = replace_once(source, 'from fastapi import FastAPI, Request',
        "from resource_budget import ResourceBudget\nBUDGET = ResourceBudget.from_environment({'AI_ANALYSIS_RESOURCE_PROFILE': 'dlpc-4cpu'})\n\nfrom fastapi import FastAPI, Request")
    source = replace_once(source, '    torch.set_num_threads(2)',
                          '    from gpu_budget import configure_torch, gpu_slot\n    configure_torch(torch)')
    source = replace_once(source, '    model = TTS(language="KR", device=os.environ.get("TTS_DEVICE", "cuda:0"))',
        '    with gpu_slot():\n        model = TTS(language="KR", device=os.environ.get("TTS_DEVICE", "cuda:0"))')
    source = replace_once(source, '            model.tts_to_file(text, speaker, str(wav), speed=0.92, quiet=True)',
        '            with gpu_slot(timeout=2):\n                model.tts_to_file(text, speaker, str(wav), speed=0.92, quiet=True)')
    source = replace_once(source, '    if engine.pending >= 2:', '    if not BUDGET.can_admit() or engine.pending >= 2:')
    # Per-request encoder/decoder workers must also fit the four-core budget.
    source = source.replace('["ffmpeg", "-nostdin",', '["ffmpeg", "-threads", "1", "-nostdin",')
    compile(source, 'server.py', 'exec')
    args.destination.mkdir(parents=True, exist_ok=False)
    (args.destination / 'server.py').write_bytes(source.encode())
    helper = (Path(__file__).parent / 'gpu_budget.py').read_bytes().replace(b'\r\n', b'\n')
    (args.destination / 'gpu_budget.py').write_bytes(helper)
    budget = (Path(__file__).resolve().parents[2] / 'src/voice_coach/backend_analysis/resource_budget.py').read_bytes().replace(b'\r\n', b'\n')
    (args.destination / 'resource_budget.py').write_bytes(budget)
    identity = dict(parentServiceSha256=SOURCE_SHA, serviceSha256=hashlib.sha256(source.encode()).hexdigest(),
                    gpuBudgetSha256=hashlib.sha256(helper).hexdigest(), cpuThreads=1,
                    resourceBudgetSha256=hashlib.sha256(budget).hexdigest(),
                    qualityVerified=False)
    (args.destination / 'dlpc-manifest.json').write_text(json.dumps(identity, indent=2) + '\n')
    print(json.dumps(identity))


if __name__ == '__main__':
    main()
