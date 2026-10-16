"""Read installed TTS synthesis identity without generating audio or approving QA."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import host_identity

ROOT = Path('/workspace/voice-coaching-tts')


def sha(path):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    host_identity.current()
    parent = json.loads((ROOT / 'manifest.json').read_bytes())
    source = ROOT / 'service/server.py'
    if sha(source) != '8f25077eab50503b914e530866225d8984899dac2dcbf2791c622d90c36ea83b':
        raise ValueError('TTS_SOURCE_DRIFT')
    manifest = dict(parent)
    manifest.update(schemaVersion='voice-coaching.tts-synthesis-identity.v1',
        revision='melo-ko-practice-dlpc-r1', parentServiceSha256=parent['serviceSha256'],
        serviceSha256=sha(source), gpuBudgetSha256=sha(ROOT / 'service/gpu_budget.py'),
        resourceBudgetSha256=sha(ROOT / 'service/resource_budget.py'),
        voice='ko-KR-practice-v1', speaker='KR', torchIntraOpThreads=1,
        torchInterOpThreads=1,
        processThreads=dict(OMP_NUM_THREADS=1, MKL_NUM_THREADS=1,
                            OPENBLAS_NUM_THREADS=1, NUMEXPR_NUM_THREADS=1),
        ffmpegThreads=1,
        mediaTools={name: dict(sha256=sha(Path('/usr/bin') / name),
            version=subprocess.check_output(['/usr/bin/' + name, '-version'],
                        text=True, timeout=10).splitlines()[0]) for name in ('ffmpeg', 'ffprobe')})
    # This explicit JSON representation is the revision fingerprint input.
    raw = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    print(json.dumps(dict(manifest=manifest, fingerprint=hashlib.sha256(raw).hexdigest(),
                         qualityVerified=False), sort_keys=True))


if __name__ == '__main__':
    main()
