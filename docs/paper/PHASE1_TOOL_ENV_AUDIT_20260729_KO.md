# Phase 1 음향 도구 환경 감사

기준 시각: 2026-07-29 KST  
플랫폼: Ubuntu 24.04, Linux aarch64, Python 3.12.3

## 결과

| 도구 | 고정 버전 | 설치·smoke | 현재 판정 |
|---|---:|---|---|
| openSMILE Python | 2.6.0 | aarch64 wheel 설치, `pip check` 통과, strict in-memory adapter에서 eGeMAPSv02 88개 finite functionals | 추출 primitive 완료 |
| Parselmouth | 0.4.7 | Ubuntu dev deb 임시 추출로 local aarch64 wheel build, `pip check`, strict in-memory contour adapter와 180 Hz Pitch smoke 통과 | 추출 primitive 완료 |
| torchcrepe | 0.0.24 | 별도 aarch64 CPU 환경, exact freeze, strict in-memory adapter와 180 Hz smoke | 추출 primitive 완료 |
| g2pK | 0.9.4 | 별도 aarch64 환경, CMUdict exact digest와 runtime download 차단, strict expected-phone adapter·공개 예문 smoke | expected-phone primitive 완료 |
| MFA | 3.4.1 metadata 감사 | local Conda/MFA 부재, 필수 Kalpy linux-aarch64 build 부재로 native standard route blocked | 미설치·플랫폼 차단 |
| UTMOSv2 | exact code/HF revision·5-checkpoint metadata 감사 | transitive artifact·license 승인 전 weight 다운로드/설치/실행 안 함 | 미설치 |
| WavLM Base+ SV | exact HF revision·5-file manifest/config/license-link 감사 | checkpoint 권리·biometric consent 전 다운로드/실행 안 함 | 미설치 |

## 실제 openSMILE smoke

`.venv-paper-acoustics`는 현재 S104가 사용하는 `.venv-scorer`와 분리했다.
16 kHz, 1초, 180 Hz 합성 sine을
`FeatureSet.eGeMAPSv02`/`FeatureLevel.Functionals`로 처리한 결과는
다음과 같다.

```json
{
  "python": "3.12.3",
  "platform": "aarch64",
  "opensmile": "2.6.0",
  "shape": [1, 88],
  "finite": true,
  "columns": 88,
  "column_order_sha256": "09a99dccd5995f20d3979302ad705c0d07d45c5a3e339f27d0cfd20e9893a3d5"
}
```

`src/voice_coach/features/egemaps.py`는 openSMILE 2.6.0,
eGeMAPSv02/Functionals, mono 16 kHz, 정확히 88개 고유 열과 finite 값을
강제한다. 파일 경로를 받거나 resampling, split 선택, cache 쓰기를 하지
않는다. 합성 backend 계약 테스트 11개와 기존 explicit feature 테스트
4개, 총 15개가 통과했고 실제 openSMILE backend에서도 위 열 순서
digest를 재현했다.

이 결과는 package와 feature-set 실행 가능성만 증명한다. AI Hub 또는
원어민 실제 음성 feature cache, reference normalization, downstream
ablation이나 성능 개선 근거가 아니다.

## 실제 Parselmouth smoke

PyPI에는 Python 3.12 Linux aarch64 wheel이 없어 첫 source build가
`patchlevel.h` 부재로 실패했다. 시스템을 변경하지 않고 Ubuntu
python3.12 개발 deb 두 개를 임시 추출한 뒤 기본/multiarch include와
library 경로를 build process에만 주입해 0.4.7 wheel을 만들었다.

- wheel SHA-256:
  `8589ea46113a899abbe77375ddacbb998c1f7e456c0584225dbadd60fdf175f1`
- installed native extension SHA-256:
  `81cfbf20dd0167f10bff5305dfe224a8fa41c2ab2bc122b5cbaa40069737e08b`
- 16 kHz, 1초, 180 Hz sine
- Pitch frame 97, voiced 97, finite true
- voiced median `180.0001702083946 Hz`

`src/voice_coach/features/parselmouth_pitch.py`는 distribution과 module
버전을 모두 0.4.7로 강제하고, mono 16 kHz PCM과 caller-explicit
floor/ceiling/time-step/무성-frame 정책만 받는다. 35개 단위 테스트와
eGeMAPS/explicit feature 회귀를 합친 50개 테스트가 통과했다. 실제
backend에서 위 180 Hz contour digest
`da3b903af0bfda557affca6a6a0d569bca64c2ff3783eab160f508d60460af49`를
반복 재현했고, 1초 무음은 `no voiced frames`로 fail closed했다.

이는 package/Praat Pitch 경로와 추출 primitive의 합성 검증이다. 실제
발화 contour agreement, 경계톤·speaker slice와 downstream ablation은
아직 없다.

## 실제 torchcrepe smoke와 합성 backend agreement

`.venv-paper-torchcrepe`에는 `torchcrepe==0.0.24`,
`torch==2.11.0+cpu`, `torchaudio==2.11.0+cpu`를 격리 설치했다.
`pip check`와 exact freeze 비교가 통과했고, strict CPU-only in-memory
adapter에서 16 kHz, 1초, 180 Hz sine의 101/101 frame이 voiced였다.
voiced median은 `181.39877319335938 Hz`이고 같은 입력·seed의 contour
SHA-256
`1ba5feb8aac92181e8a5429705ed9799278c534a41ca54c9c03156302e3944ae`를
반복 재현했다. NumPy와 PyTorch CPU RNG state를 보존했고, zero PCM은
`no voiced frames`로 fail closed했다.

두 strict adapter에는 byte가 같은 90/120/180/250 Hz 합성 PCM도 연결했다.
backend별 전체 sweep는 반복 실행에서 exact했고, 네 조건의 median F0
절대 차이는 평균 `0.677581 Hz`, 최대 `1.398603 Hz`였다. 다만 같은
10 ms 요청에서도 torchcrepe는 101 frame, Parselmouth는 97 frame을
반환했다. 이는 padding/window 정책이 다르기 때문에 실제 contour
agreement 전에 공통 시간축·interpolation·endpoint·무성 mask를
사전 고정해야 함을 보여 준다.

상세 환경과 입력 digest는
`docs/paper/PHASE1_TORCHCREPE_ENV_GATE_20260729_KO.md`, 합성 비교는
`docs/paper/PHASE1_SYNTHETIC_PITCH_AGREEMENT_SMOKE_20260729_KO.md`에
기록했다. 이 결과는 순수 sine 배선 검증이며 실제 한국어 음성 정확도,
backend 우월성 또는 Phase 1 m4 완료 근거가 아니다.

## 실제 g2pK offline smoke

`.venv-paper-g2pk`에 g2pK 0.9.4와 현재 ARM64 dependency를 고정했다.
Upstream import가 CMUdict 부재 시 자동 download를 실행하는 경계를
확인한 뒤, 전용 NLTK data root의 zip·dictionary·README 세 digest를
검증하고 runtime download를 금지하는 expected-phone adapter를 추가했다.
공식 README 공개 예문 세 개는 socket 차단 상태에서 각각 두 번 같은
compatibility-jamo token digest를 냈다.

환경·artifact·세 smoke digest는
`docs/paper/PHASE1_G2PK_ENV_GATE_20260729_KO.md`, adapter 계약은
`docs/PHASE1_G2PK_EXPECTED_PHONE_PRIMITIVE_KO.md`에 기록했다. 실제
AI Hub transcript/JSON agreement, G04, CTC checkpoint, MFA와 m1 metric은
없으며 이 expected sequence를 observed TextGrid로 사용하지 않는다.

## 라이선스 경계

openSMILE의 공개 core는 private/research/education 사용에 한정되고 상업
제품에는 별도 라이선스가 필요하다. Parselmouth/Praat는 GPL 계열이고
torchcrepe의 공개 코드·배포 metadata는 MIT다. g2pK는 Apache-2.0으로
표기되지만 설치 dependency인 KoNLPy는 GPLv3+, CMUdict는 고지·조건
보존이 필요한 별도 재배포 문구를 가진다. 따라서 이 도구 환경과 향후
feature/artifact는 각 dependency의 license/output lineage를 분리
기록하고 production dependency에 자동 편입하지 않는다.

## 다음 gate

1. 합성 smoke에서 확인된 frame-policy 차이를 반영해 공통 time grid,
   interpolation, endpoint와 무성 정책을 결과 확인 전에 사전등록하고,
   허가된 non-locked 실제 한국어 발화에서 agreement/calibration을
   실행한다.
2. g2pK의 transcript 정규화·숫자·영문·punctuation·prescriptive 정책을
   사전등록하고 expected JSON과 source를 분리한 dev agreement를
   실행한다.
3. eGeMAPS/Pitch digest와 schema를 cache 계약에 고정하고 common resampling,
   row-level privacy와 split allowlist를 사전 고정한다.
4. train/dev non-locked 소표본 cache를 만들고 기존 RMS proxy와 값의
   유효성만 비교한다.
5. 모델 선택에 쓰기 전 실제 ablation protocol과 license/output
   lineage를 동결한다.
