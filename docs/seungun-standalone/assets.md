# 독립 음향 패키지의 자산·출처

배포 source `791ca549…`, bundle `d5ccbc76…`, core manifest `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391`을 2026-10-19에 로컬 root 소유 배포 파일과 대조했다. core manifest의 82개 파일 모두 hash가 일치했다. 이 중 runtime에 필요한 source/config/contract와 추가 MFA loader를 export했으며 과거 validation 결과·fixture·운영 env·H5는 포함하지 않았다.

실제 파일 hash는 [deployed-source.json](../../standalone/seungun/manifests/deployed-source.json), 다운로드 파일은 [assets.json](../../standalone/seungun/manifests/assets.json)에 있다. 원본 manifest는 `vendor/core/release_manifest.original.json`에 보존한다. `vendor/` 원본은 직접 편집하지 않고 `local/layout.py`에서 경로 연결·device 변형·새 manifest 생성을 담당한다. 소스 파일명에 `evaluate_smoke`가 남은 것은 배포 runner가 그 모듈의 P1 음소 위치 판정 함수를 실제 import하기 때문이다. 해당 파일의 평가 CLI나 synthetic 함수는 실행하지 않는다.

| 자산 | 버전·범위 | 보관·확보 |
|---|---|---|
| Native 음소 모델 | `slplab/wav2vec2-xls-r-300m_phone-mfa_korean@e26ff9dfb62169acf445d0060ef56863c018b20e` | Hugging Face 고정 revision·SHA. 모델 card는 Apache-2.0 |
| MFA 모델 | `MontrealCorpusTools/korean_mfa@0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd` | Hugging Face 고정 revision·SHA. CC-BY-4.0 model card 포함 |
| NLTK cmudict | 배포 ZIP의 정확한 SHA·크기 | NLTK 공식 data URL에서 받고 hash 검증. URL 내용이 변하면 중단 |
| P1 정책 | 배포 policy SHA `c3c4a119…` | 작은 정책 JSON을 source 자산으로 포함 |
| AI Hub 71557 reference | 배포 speaker-balanced 집계 분포 | 집계 JSON만 포함. 녹음·화자 ID·행별 label 없음. 원자료 이용 조건을 별도로 유지 |
| Kalpy CPU build | 0.10.5, source SHA `8a895245…` | 공식 PyPI source·conda-forge recipe 기반. CPU Kaldi 5.5.1172와 연결. MIT |

B2 운영 backup과 사용자 녹음 저장소는 이 독립 실행판의 필수 설치 경로가 아니다. 공개 배포 자산을 직접 받아도 배포 bytes와 hash가 같아야 한다. 다운로드 파일은 `.part`에 받은 후 크기·전체 SHA가 일치해야 원자적으로 공개한다. 완성 cache가 손상되면 자동 재PUT/교체나 모델 fallback을 하지 않는다.

부모 정책 JSON의 `no_production_deployment`, CPU-only parity·development 집계는 **원래 정책의 한계 기록**이다. 이후 서버 배포나 이식판 생성으로 그 기록을 품질 승인으로 바꾸지 않는다. CPU/CUDA/ARM profile은 각각 별도 동등성 확인이 필요하다.

초기 설치에 인터넷은 필요하지만 계정·API 키는 요구하지 않는다. 분석 컨테이너는 `--network none`이다. 모델 가중치·로컬 cache·사용자 입력/결과·credential은 Git에 추가하지 않는다.
