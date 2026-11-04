이 문서는 2026-11-04 후속 요청으로 수행한 6개 평가의 서비스 연결 계획과 구현 인계다. 모든 점수 기준은 `EXPERT_REVIEW_PENDING`이다. 전문가 검증·운영 반영 완료 기록이 아니다.

## 계획과 구현

1. 기존 Seungun CORE를 한 번 생성하고 6개 점수·선정 근거를 계산하는 새 H5를 만든다. 구현: `six_dimension_bridge.py`, `build_six_dimension_h5.py`.
2. 정확한 점수·하위 3개·GPT 참조 근거·요청 hash를 재계산해 검증한다. 구현: 새 pinned 모듈 로딩, canonical preflight와 offline verifier 연결.
3. 기존 journal/handoff/원본 보관을 유지하며 Backend 공개 DTO와 직접 이력에 6개 결과를 전달한다. FE는 일반 결과·직접 분석·이력과 상세 화면에서 6개 점수/최대 3개 연습을 표시한다.
4. Python 컴파일·schema/identity 정적 확인, TypeScript noEmit, Java compileJava로 확인한다. 자동 테스트·실제 음성·GPT·브라우저 QA는 수행하지 않는다.

## 작업 기준과 백업

- AI: 초기 구현은 `/home/2020067/intelligentAI`의 `2c9f50a`에서 수행했다. PR 작업은 `/home/2020067/intelligentAI-six-dimension-pr`에서 원격 main `d8a7fc2`에 이번 변경만 통합했다. 기존 사용자 변경과 로컬 백업은 커밋에 포함하지 않는다.
- BE: `/home/2020067/VC-BE-six-dimension`, 초기 기준 `7e05c29`에서 구현한 뒤 원격 develop `b03eec3`를 병합했다.
- FE: `/home/2020067/VC-FE-six-dimension`, 초기 기준 `684af2f`에서 구현한 뒤 원격 main `35be3ef`를 병합했다.
- BE/FE 작업 브랜치: `feat/six-dimension-feedback-20261104`.
- 초기 원격 fetch는 프록시 해석 실패로 수행하지 못했다. 후속 커밋·푸시·PR 요청에서 인증을 프로세스 메모리에만 주입하고 해당 프로세스의 잘못된 프록시 설정을 제외하여 위 원격 HEAD를 확인·통합했다. 게시된 이력을 다시 쓰지 않았다.
- 최초 백업 외에 `systempromptbackup/service-20261104T083448Z/`에 세 저장소의 수정 전 소스/계약 2,180개를 추가 보관했다. 두 백업은 서비스 연결 전과 로컬 coach 구현 전의 서로 다른 복구 지점이다.

## 실제 실행 흐름

내부 HTTP executor와 `/direct` worker 모두 기존 admitted producer의 CORE bytes를 받는다. 선택된 H5 identity가 SIX_DIMENSION이면 새 transport/bridge를 사용하고 기존 GPT 채점은 실행하지 않는다. 기존 음성 모델·MFA·GOP의 원본 출력은 바꾸지 않는다.

bridge는 binding·패키지 lock·CORE schema를 확인한다. 입력 ACCEPT이면 기존 계산식의 측정값으로 6개 점수를 확정하고 표시 점수 오름차순/고정 동점 순서로 최대 3개를 선정한다. 모든 값과 선택은 정책 revision/hash로 추적된다. GPT는 한 번 호출해 여섯 항목 설명과 선정된 항목의 연습 방법을 작성한다. 원고·수치·단어/음소 근거를 전달하며 음성·logits·키·저장소 경로는 GPT에 전달하지 않는다.

입력 REJECT/INCONCLUSIVE/SYSTEM_FAILURE는 GPT를 호출하지 않는다. 측정값 누락은 null이며 해당 항목을 선정하지 않는다. 점수가 모두 높으면 상대적인 보완 연습으로 표현한다. 공급자 실패·잘못된 응답에서는 점수와 선정 결과를 유지하고 개인 교정을 생성하지 않는다. 입력 예산 초과도 점수를 보존하며 호출을 하지 않는다. 인계·보관 재시도는 GPT 재실행을 유발하지 않는다.

canonical preflight/offline verifier는 정책과 실제 CORE로 점수·순위를 다시 계산한다. GPT 설명의 점수 복사·항목 순서·다른 항목 근거 인용·피드백 개수를 검사한다. 자연어의 사실성 전체를 검증했다고 표시하지 않는다.

## 계약과 공개 projection

기존 request.v4/result.v6 envelope를 유지하며 새 identity에만 허용되는 `coaching.sixDimension` 분기를 추가했다. 내부 결과는 `voice-coaching.six-dimension-result.v1`, 공개 projection은 `voice-coaching.six-dimension-view.v1`이다. 기존 identity가 새 필드를 붙이거나 새 identity가 READY에서 새 필드를 생략하면 거절한다. 새 identity는 legacy 9항목 scoring record를 만들지 않는다. 종합 overallScore는 null이며 여섯 독립 점수를 보여준다.

v4/v6 schema는 AI와 BE의 기존 identity들을 보존해 통합했다. BE에만 있던 최근 H5 분류도 유지했다. AI/BE에 동일 bytes를 적용했다. 기존 writer의 payload 의미는 유지하지만 schema digest는 바뀌므로 양쪽 설치와 readiness를 함께 전환해야 한다. 옛 reader가 새 결과를 받아도 된다는 의미가 아니다.

원본 BRIDGE_RESULT에는 점수·근거·binding·요청 hash가 보관된다. 공개 DTO는 항목 ID·이름·평가 범위·점수·측정 component·선정 ID·연습 문장만 허용한다. private CORE·음소 근거 ID·파일 hash·참조 파일 경로·내부 오류는 공개 projection에서 제외한다. 직접 이력의 기존 JSON 저장 경로는 새 공개 필드를 그대로 보존한다. 새 DB column이나 migration은 필요하지 않다.

원격 develop에서 이미 제거한 완료·verifier gate는 복원하지 않는다. 새 결과의 GPT 실패 상태를 성공 피드백 전달로 기록하지 않도록 한다. AI의 canonical preflight/offline verifier 구현과 BE의 실제 호출 정책은 구분한다. FE는 신규 schema를 검사하고 하위 3개가 실제 점수 순서와 맞는지도 확인한다. 기존 결과 화면은 그대로 읽는다.

## 릴리스 identity와 준비 파일

- revision: `canonical-six-dimension-20261104-v1`
- core: `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391`
- H5: `7fbfbd4a56bd0d7a7d9c750a62c6b6e78c8654cd0ac5d721df9fe29c331a39e2`
- lock: `f4309130f20715b3c2f055121a88197e09c8ae37088e8eb2341b337f36cc65c8`
- prompt: `d385cba01552b87ea27c6d032628aa96f3abb2aa3c373d93043e72fee60b1396`
- policy: `905c923f6727b7d9c593682404a2319adc1ddad387326c687f1efa0fac664aac`

서비스 H5는 기존 DLPC GOP f6d56e core를 명시적으로 재사용한다. 로컬 sol61 H5는 검증된 계약을 읽는 빌드 입력이며 그 디렉터리를 덮어쓰지 않는다. 이 릴리스를 현재 운영 프로세스에 설치·선택했다고 주장하지 않는다. 현재 운영의 다른 성능 core와 자동 호환된다고 가정하지 않는다.

```sh
python deploy/dlpc/build_six_dimension_h5.py \
  --source /path/to/verified-existing-h5 \
  --core-manifest /path/to/f6d56e-core/release_manifest.json \
  --destination /path/to/new-output
```

생성 디렉터리는 `canonical_six_dimension_20261104_v1_h5`다. 기존 core/H5를 덮어쓰거나 default 운영 설정을 변경하지 않는다. 전문가가 정책·프롬프트·계산 코드를 수정하면 새 H5/lock/hash를 만들고 source identity·schema·BE verifier pin을 함께 갱신해야 한다. 등록 도구는 소스 계약을 갱신할 뿐 서비스 활성화나 기존 hash 교체를 수행하지 않는다.

이 H5는 음성 전용이다. 영상 근거 평가·절대 음량·한국어 경계성조·의미 단위 쉼의 품질 판단은 추가하지 않았다. 억양은 F0 변화 폭, 강도는 상대 변화 폭의 임시 점수이며 화면에도 범위를 표시한다.

## 확인 범위와 배포 전제

Python 소스 컴파일과 JSON schema 구조, identity tuple 및 dependency lock hash를 정적으로 확인했다. FE TypeScript noEmit과 BE Java compileJava를 수행했다. Gradle은 샌드박스의 wildcard IP 제한으로 처음 시작하지 못했으며, 허용된 sandbox escalation에서 기존 로컬 캐시만 사용하는 offline compileJava로 성공했다. 테스트는 실행하지 않았다.

실제 녹음 처리·GPT 생성·피드백 사실성·전문가 점수 일치·브라우저 화면·실행 성능은 개발자 QA가 필요하다. 최신 원격과 통합 후에도 Python 3.12 컴파일·동일 schema/모든 등록 identity tuple 정적 확인·FE TypeScript noEmit·BE offline compileJava를 확인했다. 저장소 QA 지침에 따라 커밋에는 `[skip ci]`를 사용한다. 운영 활성화는 별도의 검증·설치·readiness 확인 후 수행하며, 이 PR은 서비스 재기동이나 운영 배포를 수행하지 않는다.
