# Phase 1 g2pK split-currency 감사와 현행 dev 재실행

기준일: 2026-07-29 KST  
판정: **구 split 근거 superseded / 현행 project dev 16건 expected-source
diagnostic 완료 / 정확도·관측 발음·Phase 1 m1 완료 근거 아님**

## 1. 결론

기존 g2pK smoke는
`aihub71490_mvp_speaker_disjoint.jsonl`의 `split=dev`를 사용했다. 현행
논문 실험 split의 기준은 `aihub71490_modeling.parquet`이다. 두 manifest를
식별자나 경로를 출력하지 않고 322,380행 전체에 대해 다시 결합했다.

- legacy/current unique join: 322,380 / 322,380
- 중복, 미결합, JSON reference 불일치, 구조 오류: 모두 0
- 기존 고정 16건의 현행 split:
  train 11, dev 1, calibration 2, locked_test 2, stack 0
- 기존 selection digest:
  `4ce87fb7ebd6f14e36c5e56ee118b72bc36469cd85de68feac8bdeb4937ad1ea`
  exact 재현
- 기존 artifact disposition:
  `superseded_current_split_drift`

따라서 기존 artifact의 `development_only=true`와 0.9464885752688172
agreement는 **현행 non-locked dev 논문 근거로 사용하지 않는다**. 기존
artifact와 문서는 삭제하거나 덮어쓰지 않고 역사적 기록으로 보존했다.

그 뒤 현행 modeling manifest의 project dev 35,074행만 metadata 단계에서
모집단으로 고정하고, seed 42의 동일 selection policy로 16건을 선택했다.
선택이 끝나기 전 corpus source는 열지 않았다. 선택 후 그 16건의
**기대 라벨 JSON**만 열어 offline g2pK 비교를 실행했다.

## 2. source 의미 경계

이번 비교의 양쪽은 모두 기대 발음 계열이다.

- g2pK:
  `expected_g2pk_transcript_conditioned`
- AI Hub `RecordingMetadata.phonemic.phones`:
  `expected_aihub_json_annotation`

AI Hub JSON phone은 expected annotation이다. 외국어 사용자가 실제로
발음한 observed phone, 사람 gold, 정확도 정답, TextGrid realized phone
또는 acoustic posterior가 아니다. 이번 수치를 pronunciation accuracy,
관측 발음 일치율, 모델 우월성 또는 primary 선택 기준으로 표현하지 않는다.

## 3. metadata-only split-currency 감사

감사 entrypoint는 dataset root 인자를 받지 않는다. 허용 입력은 다음
세 aggregate/metadata 파일과 caller-pinned implementation뿐이다.

1. legacy metadata JSONL
2. current modeling Parquet
3. legacy aggregate artifact

expected-label JSON, audio, TextGrid와 network 접근 수는 모두 0이다.
transcript, phone sequence, row/speaker ID, source path는 artifact에
직렬화하지 않았다.

### 3.1 pinned provenance

| role | SHA-256 |
|---|---|
| legacy manifest | `6d121591f98d3916617df9efe6b0f86371c92bdd2c6c951b003cdabde6ec7a1a` |
| current modeling manifest | `32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1` |
| legacy v2 aggregate artifact | `dd42ca66dc7bd1b1c908f1f11bae1722579af1207d9f9debaeaf2985466c8e7b` |
| split audit runner | `289dd4ebeaf2104bd6215f2a552acd567d0afd06580ce7bbc60295dc921cb2c4` |
| legacy smoke runner | `2c1802391e9435594347461016e11129c7b1964cbf8083bdb7f5b1cbfdf070a0` |
| modeling manifest builder | `1687f8d54b3fbfe7dcf162f2783551038f33117809d59a027330e7eaffdae858` |
| scorer environment lock | `f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2` |

current split assignment은 manifest physical row order에서 ID와 split을
length-frame해 집계했다.

- current split assignment SHA-256:
  `038e03c6ab9f742075a5f220ceda8c20020d135951f9b8590384a9396ceff9d5`

### 3.2 전체 split confusion

행은 legacy split, 열은 current split이다.

| legacy \ current | calibration | dev | locked_test | stack | train |
|---|---:|---:|---:|---:|---:|
| calibration | 3,318 | 3,851 | 6,468 | 4,042 | 16,980 |
| dev | 3,109 | 4,198 | 8,359 | 3,480 | 15,511 |
| locked_test | 7,867 | 7,166 | 10,742 | 6,228 | 30,243 |
| stack | 3,694 | 2,942 | 6,122 | 3,298 | 14,065 |
| train | 17,118 | 16,917 | 34,621 | 18,163 | 73,878 |

legacy/current 전체 split count는 다음과 같다.

| split | legacy | current |
|---|---:|---:|
| train | 160,697 | 150,677 |
| dev | 34,657 | 35,074 |
| stack | 30,121 | 35,211 |
| calibration | 34,659 | 35,106 |
| locked_test | 62,246 | 66,312 |

이 감사가 증명하는 것은 split currency와 기존 claim의 무효화다.
agreement metric은 다시 계산하지 않았고 raw expected label도 읽지 않았다.

## 4. 현행 project dev 실행 계약

새 runner는 다음 순서를 fail closed로 강제한다.

1. modeling manifest, split audit, implementation, 두 environment lock의
   caller-provided SHA를 시작 시 검증한다.
2. Parquet의 전체 322,380행에서 schema, split, ID/JSON reference uniqueness,
   prompt digest와 split assignment를 metadata-only로 검사한다.
3. `split=dev` 35,074행에서 eligibility나 raw 내용을 보기 전에 seed 42,
   sample 16을 고정한다.
4. modeling manifest를 다시 해시해 selection 중 변경되지 않았음을
   확인한다.
5. 선택된 current dev 16건의 expected-label JSON만 연다.
6. transcript digest를 modeling metadata의 `prompt_sha256`와 비교한다.
7. 고정 punctuation, noncanonical annotation, resource 정책을 적용한다.
8. socket connect/connect-ex/sendto와 address-resolution helper를 차단한
   상태에서 g2pK와 expected-phone agreement를 실행한다.
9. 입력과 implementation/environment lock을 종료 시 다시 해시한다.
10. aggregate만 mode `0600`으로 atomic replace한다.

동일 selection policy와 seed를 유지했지만 모집단은 현행 project dev로
바뀌었다.

- current dev population: 35,074
- current selection SHA-256:
  `488f941742c0422063c950f408324d7e2717118ddd7eb0a027eca31a22d6d297`
- 선택 고정 / expected-label JSON 완료 / g2pK 비교 완료:
  16 / 16 / 16
- raw content aggregate SHA-256:
  `17a90096331fc7217edff74b0689fc6c40902b4fafbd5d998379c87f2abccdd1`
- rejection / post-attempt failure / socket attempt: 0 / 0 / 0

### 4.1 접근 증거

| current split | expected-label JSON access |
|---|---:|
| dev | 16 |
| train | 0 |
| stack | 0 |
| calibration | 0 |
| locked_test | 0 |

- dev 외 metadata-only skip: 287,306행
- audio access: 0
- TextGrid access: 0
- network socket attempt: 0
- 직렬화된 transcript/phone/row ID/speaker ID/source path: 없음

locked_test 두 건을 읽었던 기존 실행은 이 현행 실행의 입력이나 metric에
재사용하지 않았다. 새 runner는 split audit의
`superseded_current_split_drift`와 exact current split assignment를
확인하지 못하면 raw source 경계로 진행하지 않는다.

## 5. 현행 dev 16건 aggregate

| 항목 | 값 |
|---|---:|
| g2pK expected phone | 257 |
| AI Hub JSON expected phone | 238 |
| exact sequence match | 4 / 16 |
| Levenshtein distance 합 | 31 |
| macro normalized edit agreement | 0.8535406197812213 |
| row 최소 / 최대 agreement | 0.6666666666666667 / 1.0 |
| comparison aggregate SHA-256 | `aeb5194929b46cc1f1e73d3e5ac96c9493ae9e2e84abd2dbba5210725bf8d73e` |

16건의 작은 expected-source diagnostic이며 신뢰구간, population
estimate, slice, 사람 검토 또는 독립 평가가 없다. `4/16`과 agreement를
accuracy로 해석하지 않는다.

선택된 16건의 source-policy aggregate는 다음과 같다.

- annotation phone occurrence 238, canonical 238, noncanonical 0
- punctuation 포함 row 7, punctuation occurrence 7
- 고정 punctuation policy 밖의 row 0
- 최대 transcript codepoint 31, resource cap 초과 0

## 6. artifact와 구현 pin

### split-currency audit

- path:
  `artifacts/phase1/g2pk_aihub71490_split_currency_audit_20260729.json`
- schema:
  `voice_coach.g2pk_aihub71490_split_currency_audit.v1`
- file SHA-256:
  `b31b90a8b0410b8d154f6bbe8d00ed7bec1e2b6efb7ff5db04310ade8c0005f2`
- payload SHA-256:
  `8de2591110efb3f9c84ee24e9113eb41ea4a464f9e509d44b78f2f4ab586a234`
- mode: `0600`

### current dev smoke

- path:
  `artifacts/phase1/g2pk_aihub71490_current_dev_agreement_smoke_20260729.json`
- schema:
  `voice_coach.g2pk_aihub71490_current_dev_smoke.v3`
- file SHA-256:
  `bef4b8183ddaffbb3d79b3f8ca9927c93ad34e6589bdce9d05c73b782aced137`
- payload SHA-256:
  `7b21023f6fbd950bca037ece3a3ff2e46a575d6ad586275e21a50cd41ff81707`
- mode: `0600`
- 동일 command 2회 file/payload byte exact: pass

현행 dev runner의 implementation pin은 다음과 같다.

| role | SHA-256 |
|---|---|
| current dev runner | `b31be264661c54145ec0c71dbe782e734431f76990eb4dcb25d23c20bfcd939a` |
| expected-phone agreement | `c4bfc4d7ce8890910a0a23dd50462f815517d258678de5f792e68a87347b8205` |
| g2pK adapter | `887864723991f129391d2b4f54ddc6bdd3192ddc531c032ad2ca1c74ebcfd95c` |
| phone inventory | `ad87e6c8edb961dc3b031342f433143d7d1412ac07812a5b7d5fd7741e13ac3b` |
| modeling manifest builder | `1687f8d54b3fbfe7dcf162f2783551038f33117809d59a027330e7eaffdae858` |
| g2pK environment lock | `f0cb524d23d9d4be290884267a034dac1a1f203f7f76e51dffeb065f9a604785` |
| scorer environment lock | `f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2` |

실행 interpreter는 `.venv-paper-g2pk/bin/python`이고, pinned
`pyarrow==25.0.0`만 scorer environment site-packages에서 공급했다.
따라서 artifact는 두 lock을 함께 고정하며 이 hybrid runtime을 숨기지
않는다.

## 7. 실행·정적 검증

- 신규 전용 test: 7 passed
- 기존 g2pK/expected agreement/taxonomy 포함 관련 회귀:
  128 passed
- Ruff check: pass
- Ruff format check: pass
- mypy, 신규 runner 2개:
  `Success: no issues found in 2 source files`
- 실제 split audit: pass
- 실제 `.venv-paper-g2pk` current dev run 2회: pass
- current dev artifact byte-exact repeat: pass

관련 회귀 첫 시도에는 존재하지 않는 `tests/unit/test_phones.py`를
실수로 포함해 collection 전에 종료했다. 그 파일을 제외한 실제 관련
test set을 다시 실행해 128/128 통과했다. 실패한 첫 명령을 test 성공으로
계수하지 않는다.

## 8. 남은 Phase 1 gate

이번 교정으로 확보한 것은 **현행 project dev에서의 작은 expected-source
agreement diagnostic**뿐이다.

1. current dev 표본 확대 전 expected annotation과 표준발음 정책의 사람 감사
2. 숫자·영문·punctuation·descriptive option과 slice/uncertainty 사전등록
3. G04 통과 후 observed TextGrid/phone recognizer 계열의 source 분리 평가
4. MFA phone inventory mapping, OOV/boundary 정책과 alignment 감사
5. 독립 평가와 confidence interval

따라서 현재 판정은 기존 split claim을 안전하게 supersede하고 현행 dev
diagnostic을 다시 실행한 것이며, pronunciation accuracy나 Phase 1 m1
완료를 주장하지 않는다.
