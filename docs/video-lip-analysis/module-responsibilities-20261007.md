# 음성·영상 v6 추가 모듈

2026-10-07. 이번 audiovisual 변경의 디렉터리 책임을 기록한다. 전체 저장소의 다른 기능은 해당 기능 문서를 따른다.

| 경로 | 책임 |
| --- | --- |
| `src/voice_coach/backend_analysis/canonical_executor.py` | 접수된 실행의 파일 수명·병렬 분석·단일 GPT·handoff 연결 |
| `src/voice_coach/visual_feedback/canonical_service.py` | 실제 MFA와 영상 PTS 연결, private 최소 근거 조립 |
| `src/voice_coach/visual_feedback/adapters/` | 검증한 MP4의 격리된 ffmpeg/MediaPipe 프로세스, 취소·timeout 회수 |
| `src/voice_coach/visual_feedback/canonical_alignment.py` | detector index 공간의 실제 자음·모음 MFA 구간 및 sample-exact 시간 연결 |
| `src/voice_coach/visual_feedback/reference_catalog.py`, `reference_distance.py` | pinned 참조 출처·별도 보정 계약과 제약 WDTW |
| `src/voice_coach/visual_feedback/reference_preparation.py` | 참조 후보 데이터와 명시적 전문가 검수의 catalog 편입 서비스 |
| `src/voice_coach/visual_feedback/canonical_verification.py` | 같은 설치 자산·MFA·정규화 특징으로 offline 재계산 |
| `src/voice_coach/backend_analysis/canonical_audiovisual_result.py` | 기존 음성 의미 검증 재사용, v6 크기 예약·public 요약 |
| `deploy/dlpc/build_audiovisual_core.py`, `audiovisual/mfa_alignment.py` | 기존 Seungun/P1/GOP를 보존하는 새 frozen core 소스 패키징 |
| `deploy/dlpc/build_audiovisual_h5.py`, `audiovisual/` | 전문가 프롬프트를 유지한 영상 관찰 추가와 pinned catalog/config |
| `deploy/dlpc/prepare_lip_reference.py` | 개발자가 수행하는 실제 참조 추출·검수 편입 CLI. 운영 활성화 없음 |
| `docs/contracts/` | 양쪽 저장소가 동일 바이트로 유지하는 request/result/handoff/receipt 계약 |

의존 방향은 HTTP/executor → application service → feature adapter와 순수 계산이다. Backend가 소유·등록·receipt·영속 저장/조회·B2 archive를 맡고 FE는 검증된 public view만 표시한다. 상세 흐름과 미완료 운영 조건은 [v6 인계](../video-lip-analysis/audiovisual-v6-handoff-20261007.md)를 따른다.
