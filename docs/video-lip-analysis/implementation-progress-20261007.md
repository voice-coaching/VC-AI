# 음성·영상 통합 구현 진행 기록

2026-10-07. **v6 실행·전달·저장·조회·FE 연결 후보 구현. 운영 배포 및 실제 영상 품질 검증은 미완료.**

## 작업 위치와 PR

- AI: `CreatorTemp/ai-audiovisual-20261007`, [main 대상 PR #34](https://github.com/voice-coaching/intelligentAI/pull/34).
- Backend: `CreatorTemp/backend-audiovisual-20261007`, [develop 대상 PR #114](https://github.com/voice-coaching/VC-BE/pull/114). PR #113의 DLPC 변경을 기반으로 한다.
- FE: [dev ?? PR #67](https://github.com/voice-coaching/VC-FE/pull/67). `origin/dev=ff3dec9`에서 `CreatorTemp/frontend-audiovisual-20261007` 분리. 사용자 원래 checkout 변경은 보존했다.

## 구현 범위

| 경계 / 파일 | 실제 변경 |
| --- | --- |
| AI `http_models.py`, `http_runtime.py`, `http_api.py` | request v4/result v6 접수, 미디어 receipt, 인증된 양쪽 readiness·schema digest·H5 pin 확인. 기본 비활성 |
| AI `canonical_executor.py`, `visual_feedback/canonical_service.py` | 준비 PCM의 Seungun 실행과 영상 특징 추출 병렬화. 같은 시도 MFA 구간과 PTS 연결 후 기존 GPT 한 번에 전달 |
| AI `canonical_video_features.py`, `canonical_feature_worker.py` | SHA 검증, 실제 PTS, RGB 스트리밍, 네트워크 차단 MediaPipe worker, timeout/cancel 시 subprocess 회수. RGB 저장 없음 |
| AI `canonical_alignment.py` | 실제 자음 MFA index/구간 유지. MP4 검증 디코딩 PCM·Backend PCM·core 준비 PCM의 sample hash/길이 일치와 연속 audio PTS가 확인될 때만 시간 연결 |
| AI `reference_catalog.py`, `reference_distance.py`, `audiovisual_evidence.py` | pinned 참조/보정 loader, 같은 phone/role/단어 context/mapping 비교, 제약 WDTW, 참조 median, 별도 보정. 자기 영상 비교 제외, 기준 없으면 null |
| AI `canonical_verification.py`, `canonical_audiovisual_result.py` | 보관된 최소 정규화 좌표·PTS·MFA·참조로 재계산, 기존 음성 검증 재사용, 영상 association/digest 결속 |
| AI `coaching_input.py`, `build_audiovisual_h5.py` | 전문가 음성 원문/채점 유지, 영상 관찰을 추가한 H5 소스 후보. GPT에는 첫 12개 관찰과 생략 수. 원시 좌표/미디어 경로/비밀값 제외 |
| 공통 `docs/contracts/` | request v4/result v6/handoff v2/media preparation v1/visual coaching input v1 |
| Backend 접수·V41–V43 | 같은 등록 transaction의 receipt, execution별 immutable snapshot, v6 dispatch, artifact·용량 예약 확장 |
| Backend 전달·조회 | v6는 검증 없는 조기 공개를 사용하지 않음. spool/DB offline 검증 후 공개, 실제 result-contract, public view v3, B2 archive |
| Backend 삭제·재시도 | 기존 삭제 outbox에서 v6 실행 deadline까지 PCM/MP4 삭제 유예. 재시도 품질/profile/readiness 재확인 |
| FE API/parser/lip-practice/CanonicalLipResult | VIDEO capability+v6 헤더, 기존 analyze URL/body, view v3 엄격한 파싱, 음성과 별도 입술 상태·구간·점수 표시. null은 0이 아님 |

## 초기 후보의 한계

- **v6는 영상 recording 전용**이다. 음성은 v5를 사용한다. 초안의 v6 audio-only 지원은 이번 후보에서 제공하지 않는다.
- frozen core가 노출하는 실제 MFA는 **자음 위치**다. `alignmentScope=CANONICAL_PRONUNCIATION_EVIDENCE_CONSONANTS`. 모음 시간을 CTC로 대신 만들지 않는다.
- 초기 catalog는 `references=[]`, `calibrations=[]`인 **관찰 전용**이다. 사용자 지정 `testvideo1.mp4`의 특징·MFA 추출 및 구간 검수는 아직 수행하지 않았다.
- GPT에는 관찰을 추가하지만 `correctiveClaimsAllowed=false`다. 관찰만으로 혀 위치·발음 원인·교정 행동을 확정하지 않는다.
- 연구용 유사도와 보정 점수를 구분하고 음성 종합 점수에 합산하지 않는다. public 상세는 앞 128개와 생략 수, private artifact에는 전체 최소 근거를 유지한다.
- 무결성 불일치는 실패. 영상 오류/timeout/동기화 불확실성은 영상 상태로 남기고 유효한 음성 결과는 유지한다.
- verifier는 인증된 producer 측정값의 일관성을 확인한다. MediaPipe 실행 자체나 전문가 보정의 타당성을 증명하는 것은 아니다.

## H5 후보 pin

- core: `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391`
- H5: `d2a30ff826f275e4c678cb2b50f113371220f47d9145041aadf826745e4f2739`
- lock: `8126e24d7ed1d0a6cfd0cab20eceff570f8b7fa04767d44471f435a38425c242`
- prompt: `c727910b9e30c1648adb120cbb5f8ef2178c806285331dfd095fcc39c5aedfee`

기존 announcer package에서 builder로 소스 후보를 생성했다. 기존 frozen 파일은 덮어쓰지 않았다. 새 identity는 읽기/검증에 등록했으며 `native_execution.APPROVED_NATIVE_EXECUTIONS`에 추가하지 않았다. 개발자 QA 완료를 허위로 등록하지 않는다.

## 남은 실제 작업

1. 개발자 DLPC 실영상·GPT·offset/VFR/회전/다중 얼굴/취소·부분 실패·중복/재시도/삭제 및 CPU/RAM/TTS 영향 QA. 45초 영상 제한·60초 GPT/handoff 예산·pose 임계값은 검증 전 후보 값이다.
2. 지정 참조 영상의 실제 PCM/MFA/PTS/특징 추출과 자음 구간 전문가 검수. 별도 화자·발음 자료로 보정 후 catalog/새 H5 pin 생성. 한 영상으로 전 음소 보정을 승인하지 않는다.
3. 모음까지 지원하려면 frozen core의 실제 MFA 출력 확장 구현·검증이 필요하다. 현재는 자음 계약이다.
4. Backend 담당자의 V41–V43 검증/적용, verifier 설치, QA 근거에 따른 실행 identity 등록, DLPC 복구 bundle 갱신과 운영 전환.

## 확인 범위

Python 소스 컴파일, 공통 JSON schema 구조/로컬 참조/바이트 일치, Backend `compileJava`, FE `tsc --noEmit`, diff 정적 확인을 수행한다. **자동 회귀 테스트·브라우저 QA·실제 미디어 decode·MFA/GOP·GPU·GPT 생성·DB migration·운영 배포는 실행하지 않았다.** AGENTS의 개발자 직접 QA 규칙을 따른다.

?? builder? ? JSON ??? ???? LF? ????. Windows/Linux checkout ??? H5 pin? ???? ??? ???? ??? ?? ????. ? pin? ? ?? ?? ????.
