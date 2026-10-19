#!/usr/bin/env python3
"""Windows/macOS host launcher. Only prepare/fetch permit container networking."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--cache', type=Path, default=ROOT / '.cache')
    actions = parser.add_subparsers(dest='command', required=True)
    actions.add_parser('prepare')
    actions.add_parser('fetch')
    actions.add_parser('doctor')
    analyze = actions.add_parser('analyze')
    analyze.add_argument('--audio', type=Path, required=True)
    analyze.add_argument('--text', required=True)
    analyze.add_argument('--output', type=Path, required=True)
    analyze.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    architecture = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'amd64'
    if args.device == 'cuda' and (architecture != 'amd64' or platform.system() == 'Darwin'):
        parser.error('CUDA requires Linux/Windows WSL2 on amd64 with NVIDIA container support')
    image = f'intelligentai-seungun-local:{architecture}-{args.device}-20261019'
    try:
        engine = subprocess.run(['docker', 'info', '--format', '{{.OSType}}'],
            check=True, capture_output=True, text=True)
        if engine.stdout.strip() != 'linux':
            parser.error('Docker Desktop must use Linux containers (Windows: enable WSL2 backend)')
        if args.command == 'prepare':
            subprocess.run(['docker', 'build', '--platform', 'linux/' + architecture,
                '--build-arg', 'TARGETARCH=' + architecture,
                '--build-arg', 'DEVICE=' + args.device, '--tag', image,
                '--file', str(ROOT / 'container/Dockerfile'), str(ROOT)], check=True)
        args.cache = args.cache.expanduser().resolve()
        args.cache.mkdir(parents=True, exist_ok=True)
        command = ['docker', 'run', '--pull', 'never', '--rm', '--init', '--platform', 'linux/' + architecture,
            '--mount', f'type=bind,source={args.cache},target=/assets' +
                (',readonly' if args.command in ('analyze', 'doctor') else ''),
            '--tmpfs', '/tmp:rw,exec,mode=1777', '--shm-size', '1g']
        if args.device == 'cuda':
            command.extend(['--gpus', 'all'])
        if args.command in ('analyze', 'doctor'):
            command.extend(['--network', 'none'])
        inside = ['fetch' if args.command in ('prepare', 'fetch') else args.command]
        if args.command == 'analyze':
            audio = args.audio.expanduser().resolve(strict=True)
            if not audio.is_file() or audio.stat().st_size > 20 * 1024**2:
                parser.error('audio must be a file of at most 20 MiB')
            output = args.output.expanduser().resolve()
            if output.exists():
                parser.error('output must be a new directory; existing results are never overwritten')
            output.mkdir(parents=True)
            # UUID/job paths, scratch, locks and sockets stay on Linux filesystems.
            command.extend(['--mount', f'type=bind,source={audio.parent},target=/input,readonly',
                '--mount', f'type=bind,source={output},target=/output'])
            inside.extend(['--audio', '/input/' + audio.name, '--text', args.text,
                '--output', '/output', '--timeout', str(args.timeout), '--device', args.device])
        subprocess.run([*command, image, *inside], check=True)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({'status': 'failed', 'code': 'HOST_COMMAND_FAILED',
            'detail': type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
