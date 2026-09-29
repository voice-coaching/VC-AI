# 배포 관측 근거 — 2026-09-29

[목차](README.md) · [처리 흐름](pipeline.md) · [설정·계약](contracts-and-limits.md)

## 관측 대상과 범위

| 항목 | 관측 |
|---|---|
| 원격 확인 시각 | 2026-09-29 14:47:07~14:50:04 UTC / 23:47:07~23:50:04 KST |
| Pod | `s8hb5b0l5k4qlx` / `intelligentai-a40-int8-120gb-20260915` |
| 관리 API | `RUNNING`; `GET https://rest.runpod.io/v1/pods`에서 필요한 필드만 조회 |
| GPU | Pod에서 `nvidia-smi --query-gpu=name`으로 NVIDIA A40 확인 |
| 실행 root | `/opt/intelligentai/app` |
| 배포 metadata | `/opt/intelligentai/deployment.json` |
| `sourceRevision` | `5ea4712e55f231450b2120c7121dfaa3ade1ed6f` — metadata의 원문 식별자 |
| `baseRelease` | `15534800bcc3f730d6fffeb9aa98d3ee91d3ab18563b0dc5f0fad4dff6eb2887` |
| `overlaySha256` | `f3b40b726959b9df69762469b0d61a4c7682d997fd1f259886678f996e5e5ba4` — metadata 값; 전체 archive 재해시 아님 |
| runtime 설정 | `VOICE_COACH_BUILD_REVISION`이 위 sourceRevision과 일치 |
| supervisor | `/run/intelligentai/services.json`: `api` ready, 관측 PID 1911, supervisor PID 1910 |
| 비생성 health | `/health` HTTP 200 `alive`, `/readyz` HTTP 200 `ready` |
| 로컬 조사 HEAD | `76f6933a117e35e335ff9c0b3dfc418ec0d7f102`, branch `main`; 시작 시 작업 트리 변경 없음 |

API 포트와 SSH 호스트 키가 전날과 달라져 처음에는 접속을 중단했다. 사용자가 RunPod 웹 터미널에서 제공한 ED25519 지문과 접속 대상 지문이 일치함을 확인한 후 접속했다. API 키·private key·서버 토큰은 문서에 저장하지 않았다. 주소·포트·PID는 재접속 기본값으로 사용하지 않는다.

관리 API 인증/필드 정의는 [RunPod 공식 List Pods](https://docs.runpod.io/api-reference/pods/GET/pods)를 참고한다. 이 API 문서는 이 프로젝트의 입술 분석 구현을 설명하는 근거가 아니며, 구현은 아래 실제 배포 파일에서 확인했다.

이번 health는 liveness/readiness 응답 확인이다. 인증 `/health/services` 상세 의존성 응답, 신규 영상 처리, GPT 생성, TTS 상태는 재검증하지 않았다. 준비 응답만으로 입술 분석 완주나 품질을 주장하지 않는다.

## 활성 자산

`artifacts/visual_feedback/releases/production_visual/release.json`을 읽었으며 세 하위 파일의 실제 SHA-256이 manifest의 각 값과 일치했다.

| 자산 | 파일 SHA-256 |
|---|---|
| `runtime_bundle.json` | `6420c72161085cada2e6a166fd568fc26c7a78a409c6ea8c31c6f98ba2b2c04c` |
| `video_qc_decision_policy.json` | `987a2aa0a8818fe0b55817b6c4cbdc33f26537c259d26b5f14374965c6126956` |
| `av_sync_decision_policy.json` | `567561177ff644e53eebd2acb029260081efbfd4110a610a49c80a67b6490fd4` |
| `artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json` | `8e98ce3c92310daf59edafd8e2f2d2235247d3e708eadb3144ad4a254b0f7f34` |
| `models/mediapipe/face_landmarker.task` | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |

랜드마크 모델 크기는 3,758,596 bytes이며 composition에 고정된 모델 digest와 일치했다. 이 검사는 모델 추론 실행이 아니다.

JSON 문서 내부의 canonical digest와 파일 바이트 SHA는 다를 수 있다. 아래 값은 내부 필드의 원문이며 위 파일 hash와 혼용하지 않는다.

| 내부 식별자 | 값 |
|---|---|
| release status/id/schema | `enabled` / `production_visual_v2` / `voice_coach.production_visual_release.v3` |
| `release_sha256` | `d3f5ef69b76a027cfcd8d59104d733b661f0125dc2ce465a9e532f7c21b271c6` |
| bundle id/revision | `universal_visible_lip_runtime_v1` / `same_cohort_user_video_v1` |
| `bundle_sha256` | `67a5b1a332b5e5b580b848f636dd147190917698b0cd4a6e73f136a87569aa2e` |
| geometry artifact id | `aihub68_mediapipe_24point_geometry_v2` |
| geometry `artifact_sha256` | `8eb81c4068db56934dd4a4e69c388eb2d7011d55d8376fe37394cf3c8eb8ff55` |
| geometry revision | `aihub68_validation_lip_interjection_v2` |

## 실행 코드 식별자

아래는 `/opt/intelligentai/app/`에 대한 상대 경로와 **원격 파일 바이트** SHA-256이다. `BA`는 `src/voice_coach/backend_analysis/`, `PA`는 `src/voice_coach/pronunciation_attempts/`, `VF`는 `src/voice_coach/visual_feedback/`를 뜻한다.

| 경로 | SHA-256 |
|---|---|
| `BA/production.py` | `37328367f51928b12af17a4085b021d2952f94f761e4951c17256449766e9278` |
| `BA/service.py` | `7d9e0e5bd5623844e80ab6643e1f25bf1ad781a80c08f76c12751eda1d8111df` |
| `BA/adapters/visual.py` | `c71763d907cbd13c4c00524864765457aa6e73063456fb0faf4b878245578deb` |
| `BA/coaching_evidence.py` | `413eeacba67b88fb37338502e0bf6b49f5b3ba25600c9b794963779269b38ab2` |
| `BA/coaching.py` | `23210dde720b01fe53f26b339a0f98306b519a766bd5892056247c8f16875979` |
| `PA/composition.py` | `b6c903b3ac4454608000f9930115d54f88828ceafcfdd4fcc4c6269efb59fceb` |
| `PA/services/video_attempt_facade.py` | `9883d55fa66bca7573a0701f0560b51b1b9e36be70787848ad43d9e2310f29ae` |
| `PA/adapters/runtime.py` | `9eaab7ab1fb6c766f6aa1d0ea02d744d2a857db151e4c4fb8dde38423231cf4b` |
| `PA/adapters/production_visual_sidecar.py` | `395573799fc5f7a1bb3c5a2ae6cb059f2b0a74043c9f5ee3e8dc362953ba3c9d` |
| `PA/adapters/closed_beta_lip_trajectory.py` | `da80136e4d021317412cd3753699e82e2baa17a686c510d0c587055cf0ca7ff4` |
| `PA/adapters/mediapipe_lip_landmarks.py` | `ebe378cfa9f289fcdc85ac33a089199c6d3327ac6de36f6b50f0d1085171632e` |
| `PA/adapters/video_qc.py` | `6e4bba93ffc4186eee4f0047a774fcadadf3110425cc38d558bf105a4259d5da` |
| `PA/adapters/lip_audio_av_sync.py` | `3dfa5c1715be5b9d8081ae21239e4cfa68ab3c722ea2793de558893ed211f460` |
| `PA/adapters/ffmpeg_av_container.py` | `fcf4301e0d47d25cc34455ae66fe42e7696e027f0bdddd6394e6e898079aa6b7` |
| `PA/phone_window.py` | `0f329e8ea379cc899ec4a1f010ef133ec73c7904c0608a00c98f4d03fd69a3dc` |
| `VF/services/p6_lip_trajectory.py` | `263b8c38220b010aac87e1dd42f121ae96756909ef67d4e1757d1fd5883ee681` |
| `VF/adapters/production_visual_release.py` | `974365f38c6a20c5c74f8b7e45580872f87546ac6f9e262b3ceccc4248786c40` |

### 로컬·영구 overlay 대조

로컬 Windows 파일은 CRLF 때문에 바이트 hash가 다르다. 위 표에서 `production.py`를 제외한 파일은 **LF로 정규화한 로컬 내용**이 원격 실행 사본과 일치함을 확인하고 세부 소스를 읽었다. `production.py`는 정규화 후에도 다르므로 원격 파일 전문을 직접 읽었다. 소스를 동기화하거나 덮어쓰지는 않았다.

영구 `/workspace/intelligentai/deployment/int8-overlay`와 직접 바이트 대조한 결과:

- 표의 첫 9개 파일(`BA/production.py`부터 `PA/adapters/production_visual_sidecar.py`까지)은 overlay에 존재하며 실행 사본과 일치했다.
- 나머지 8개 파일은 overlay에 **없었다**. 해시 불일치라고 보고하지 않는다. 실행 사본에서 관측했으며 base release의 개별 파일까지 추적·재해시하지는 않았다.
- overlay는 전체 앱 사본이 아니므로 이 조사만으로 완전한 재생성·복원 가능성을 입증하지 않는다.

## 후속 재확인 절차

1. 관리 API로 현재 Pod·상태·SSH 연결값을 다시 확인한다. 중지된 GPU를 문서 확인을 위해 시작하지 않는다.
2. SSH 호스트 키를 검증한다. 키가 바뀌면 신뢰된 별도 경로로 지문을 확인하고 이전 known_hosts 기록을 무조건 삭제하지 않는다.
3. `deployment.json`, supervisor 상태, 비생성 health와 지정된 비밀값 아닌 runtime 설정을 읽는다.
4. 실제 `/opt` 코드 hash, 활성 release manifest 및 세 하위 파일 hash, 모델·geometry 자산을 대조한다.
5. 호출 경로가 여전히 `analyze_coaching_observations`인지, 출력의 교정 금지 플래그가 유지되는지 확인한다.
6. 새 관측 시각·revision을 기록한다. 서비스 로그를 추가 조사하면 요청 ID·사용자 대본·미디어·인증값이 섞일 수 있으므로 필요한 구조 필드만 공유한다.

자동 테스트·실제 영상 추론·GPT 생성은 이 문서의 정적 확인 명령에 포함하지 않는다. 이번 작업에서는 코드/설정 변경, 모델 다운로드, 로그 원문 복사, 서비스 재시작, commit/push/PR/배포를 수행하지 않았다.
