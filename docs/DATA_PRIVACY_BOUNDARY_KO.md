# 연구 데이터 개인정보·반출 경계

최초 작성: 2026-07-29 KST  
상태: 실행 중인 강제 정책

## 목적

AI Hub 71490의 원시 `UserID` 필드를 제거한 것만으로는 비식별화가
완료되지 않는다. 실제 점검에서 원본 file stem prefix가 `UserID`와
연결될 수 있음을 확인했다. 따라서 파일의 위치와 역할을 기준으로 접근
경계를 나누고, 모델 입력·결과 artifact로 넘어가는 순간에 식별자와
원천경로를 제거한다.

이 문서는 라이선스상 재배포 허가를 대신하지 않는다. 개인정보 점검을
통과한 파생물도 해당 데이터 라이선스가 허용할 때만 외부에 제공한다.

## 데이터 구역

| 구역 | 예 | 허용 내용 | 외부 반출 |
|---|---|---|---|
| R0 원천 제한구역 | `data/restricted/` WAV/JSON/TextGrid | 원시 ID, 음성, 전사, annotation | 금지 |
| R1 운영 제한구역 | raw/enriched/modeling manifest, 수동 감사 worksheet | 원천 상대경로, file stem, 상세 interval | 금지 |
| D1 비식별 실험구역 | explicit/SSL feature cache, prediction | namespaced SHA-256 발화 ID, 가명 화자 ID, 허용 특징·label·slice | 라이선스·개인 연구 scope attestation 별도 확인 |
| A1 집계 결과구역 | metric/report, 표·그림, data card | count, 분포, 성능, CI, checksum | 검토 후 가능 |
| C1 공개 코드구역 | `src/`, `scripts/`, `configs/`, `docs/` | 코드, 상대적인 예시 경로, schema, 집계값 | 가능 |

R0/R1/D1 및 실험 artifact는 `.gitignore`로 Git 추적에서 제외한다.
접근권한, 보존기간, 백업·폐기 정책은 개인 연구 보안 정책과 데이터 약관을
따른다. 신규 참여자 모집·중재·RCT를 선택할 때는 H00 참여자 연구 경로와
해당 동의·기관 요구를 추가로 따른다.

## D1 캐시 강제 계약

허용 식별자는 다음 둘뿐이다.

- `utterance_id_sha256 =
  SHA-256("voice-coach.aihub71490.utterance.v1:" + raw_file_stem)`
- `speaker_id_sha256`: manifest 생성 단계에서 namespace를 붙여 만든
  SHA-256 가명 화자 키

원본 file stem을 그대로 SHA-256한 값과의 사전계산 공격을 어렵게 하기
위해 고정 namespace를 포함한다. 이 변환은 가명화이며 익명화를 보장하지
않으므로 D1도 연구 데이터로 관리한다.

다음 열과 그 값은 D1 캐시·prediction·metric report에서 금지한다.

- `UserID`, `id`, `utterance_id`, 원본 file stem
- `audio_path`, `json_path`, `textgrid_path`, `source_paths`
- prompt 원문, 원시 JSON metadata, 국적처럼 재식별 위험이 큰 자유문자열
- 학습 입력으로 오인될 수 있는 realized phone, alignment operation,
  error tag/count

마지막 항목은 개인정보뿐 아니라 label leakage를 차단하기 위한 규칙이다.
필요한 정답 열은 feature 열과 별도 role로 선언하고 trainer는 allowlist만
읽는다.

## 생성·반출 검사

> WP4 S104–S108의 단계별 split 접근 역할은
> [WP4 S103 이후 실행 계약](./WP4_POST_S103_EXECUTION_CONTRACT_KO.md)을
> 아래의 일반 `train/dev` 제한보다 우선 적용한다. 이 예외는 S106의
> stack과 S107의 calibration 전용 runner에만 적용한다. R0/R1/D1/A1/C1
> 구역, 금지 입력, 가명화, 반출과 원자 publish 규칙은 이 문서가 우선하며
> 완화되지 않는다.

모든 feature builder와 trainer는 다음 검사를 실패 시 즉시 중단한다.

1. 입력 manifest와 prompt 예약 artifact의 SHA-256 일치
2. 허용 split이 train/dev인지 확인하고 stack/calibration/locked-test
   metric 접근 차단
3. QC flag와 예약 prompt 제외
4. 출력 schema에 금지 열이 없는지 확인
5. 발화·화자·prompt ID가 소문자 64자리 SHA-256 형식인지 확인
6. NaN/Inf, 행 수, split 수, label 결측, shard checksum 기록
7. 임시 파일에 쓴 뒤 성공할 때만 원자적으로 publish
8. 실패·중단 때 미완성 `.part` 제거와 failure report 기록

공개 후보 artifact는 추가로 문자열 표본/전수 scan, 라이선스 확인,
독립 리뷰를 거친다. checksum은 재현 식별자이지 공개 허가가 아니다.

## 2026-07-29 교정 기록

explicit feature cache의 초기 전량 실행을 5,000건 시점에 점검하면서
원본 file stem prefix와 `UserID`의 연결 가능성을 확인했다.

- 실행을 즉시 중단했다.
- 공개·최종 publish 전인 임시 캐시와 이전 smoke 캐시를 폐기했다.
- 발화 join key와 출력 schema를 `utterance_id_sha256`으로 교체했다.
- smoke utterance 100건과 phone 608건에서 해시 형식과 금지 열 미포함을
  재검증했다.
- modeling manifest를 “privacy-safe”가 아닌 R1 운영 제한 artifact로
  재분류했다.
- 전량 cache는 새 schema로 처음부터 다시 생성한다.

이 교정으로 기존 modeling manifest의 checksum은 그대로 재현 식별자로
유지하지만, 해당 파일을 공개 가능한 artifact로 해석하지 않는다.

## 사람 평가 데이터

전문가 재평가와 원어민 녹음은 개인 연구 scope attestation과 각각 별도의
동의·철회, 목적 제한, 보존·폐기, 접근권한 계획이 동결된 뒤 수집한다.
학습자 파일럿은 참여자 연구 경로의 기관 승인과 동의가 선행돼야 한다.
reviewer와
participant ID는 연구별 namespace를 붙인 가명 키로 저장하고, 대응표는
연구 데이터와 분리한다. 음성·자유서술 notes는 자동 비식별 결과만 믿지
않고 scope attestation에 지정된 담당자가 반출 전에 검토한다.
