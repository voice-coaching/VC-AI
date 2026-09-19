# RunPod 독립 예문 TTS 운영 인계

관측: 2026-09-19 16:46 KST. 사용자 승인으로 TTS 전용 토큰을 AWS에 SSH 전송·비공개 저장하고 nginx exact 경로를 적용했다. 비밀값은 문서와 저장소에 없다.

TTS는 음성·영상 분석이나 GPT 피드백 파이프라인과 별도 서비스다. 코드 정본은 VC-BE `deploy/example-tts/server.py`, `manager.py`, runtime manifest이며 intelligentAI 분석 소스를 교체하지 않았다.

| 항목 | 운영 위치/값 |
| --- | --- |
| Python 환경 | /opt/voice-coaching-tts/venv |
| 서비스 코드 | /workspace/voice-coaching-tts/service |
| Melo 코드 | /workspace/voice-coaching-tts/MeloTTS, commit 209145371cff8fc3bd60d7be902ea69cbdb7965a |
| 모델 revision | 0207e5adfc90129a51b6b03d89be6d84360ed323 |
| 합성 revision | melo-ko-practice-r1 |
| 장치/voice/rate | cuda:0 / ko-KR-practice-v1 / 0.92 |
| manifest | /workspace/voice-coaching-tts/manifest.json |
| fingerprint | 9474d12bafea63f2344e0e2e48eccbf57a512a9be717832248eab6397f8f7f0b |
| 토큰 | /workspace/voice-coaching-tts/secrets/token, 0600, 출력 금지 |
| API | 127.0.0.1:8082, POST /v1/tts/synthesize |
| 관리 상태 | /run/voice-coaching-tts/manager.pid, server.pid, manager.lock, server.log |

AWS `ai.voice-coaching.site/v1/tts/synthesize`의 nginx exact location이 loopback 18082로 전달한다. EC2 전용 SSH 터널이 RunPod loopback 8082에 연결한다. 사용자 JWT나 분석 callback 토큰 대신 별도 Bearer 토큰을 사용하며 기존 분석 8080 경로는 보존했다. RunPod health도 전용 인증을 요구한다.

기존 DB 예문 5개가 실제 AWS Java worker를 통해 첫 시도에 MP3로 생성·저장됐다. 5건 모두 digest 검증이 일치했고 AWS health UP, RunPod 분석 health alive를 확인했다. 이는 전체 분석 동시 부하나 발음 품질 QA를 뜻하지 않는다. 공개 음성은 DB의 실제 바이트 digest에 대한 검토자 승인 후 제공한다. 현재 검수 기록은 0건이다.

독립 manager는 TTS child만 관리한다. 기존 intelligentAI supervisor에 TTS를 추가하거나 분석 서비스를 재시작하지 않았다. 장애 확인 시 pid 파일의 실제 프로세스 정체를 확인하고 TTS에만 조치한다. CLOVA를 자동 재시작하지 않는다. 팟 재생성 시 `/opt` 환경과 독립 manager startup 복원은 검증되지 않았으므로 재기동 보장을 주장하지 않는다.

AWS 설정·토큰 회전·rollback의 정본은 VC-BE `docs/architecture/example-tts-operations-20260919.md`다. 이 AI 저장소에서는 이번에 문서만 추가했으며 모델 변경·QA 자동화·코드 배포를 수행하지 않았다.
