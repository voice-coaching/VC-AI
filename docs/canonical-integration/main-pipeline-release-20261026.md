# 최적화 변경의 main 통합과 coordinated 배포

최신 main에 이미 머지된 v6·ingress 수정과 standalone 패키지를 유지하고, 후속 GPT/MFA
최적화·운영 기록·기존 테스트 삭제를 `release/pipeline-sol61-main-20261026`에서 통합한다.
별도 작업 공간의 미커밋 내용은 덮어쓰지 않는다. 품질 우선이라는 사용자 결정에 따라
direct의 **gpt-6.1-sol / low / default**를 유지한다.

실행 worker·HTTPS 재사용, PCM 준비, 정적 도구 정보, 요청 소유 lexicon FST 복사와 감시
상한은 [기존 구현](pipeline-latency-implementation-20261025.md), 실제 시간과 표본 한계는
[Sol 측정](sol61-light-timing-20261026.md)을 따른다. 이번 통합에서 새 음성·GPT 호출,
단위·회귀·화면 테스트나 fixture·mock은 만들거나 실행하지 않는다.

## 통합 시 정합화

- 새 Core/H5 tuple을 private v4와 v6의 association·audioAssociation·sourceIdentity에
  함께 등록한다. 기동 전 실제 identity 확인도 두 schema를 모두 거친다.
- 사용자 지시에 따른 테스트·fixture 등 251개 파일 삭제와 pytest 전용 설정·의존성 삭제를
  유지한다. CI는 문서·소스 핀·의존성 설치·이미지 빌드 범위만 수행한다.
- 현재 AGENTS가 요구하는 개발 규칙 디렉토리를 금지하던 오래된 제약을 정정했다.
  사라진 문서·워크플로 링크는 실제 현황으로 고쳤다. 녹음·모델·비밀 자료의 추적 금지는 유지한다.
- 현재 코드·API 문서·테스트 의존성 삭제로 바뀐 연구 설정의 source pin을 정합화한다.
  네 개의 정확한 과거 fail-closed 설정은 원본 bytes를 유지하고 기존 역사 상태 검사에
  삭제된 테스트 전용 lock 의존성의 정확한 이전 pin만 추가한다. 연구 결과·승인·수치 정책을
  새로 만든 변경이 아니다. 삭제한 의존성을 다시 설치하지 않는다.

프로젝트 제약, 현재 소스 pin 659개, 핵심 파일 14개 mypy가 통과했다. 실제 canonical
Python·서비스 UID에서 Sol/내부 조합의 두 schema·Native 허용·전체 자산 SHA·동결 import를
확인했다. 이를 실제 생성·품질 검토 통과로 해석하지 않는다. 최종 PR의 검사 결과와 운영
receipt가 실제 머지·배포 여부의 근거이며, 이 절차 설명 자체는 배포 완료 증거가 아니다.

## coordinated source 배포

일반 [Actions client](../../deploy/dlpc/github_deploy.py)는 frozen schema·의존성·대규모
변경을 거부한다. 이번처럼 명시 승인된 통합에는 root 소유
[coordinated_release.py](../../deploy/dlpc/coordinated_release.py)를 사용한다.

1. 정확한 Git commit의 source·blob ID·mode·SHA와 기존 source의 SHA를 private payload로
   준비하고 모든 Python 구문·JSON을 확인한다. role secret·사용자 녹음·모델은 넣지 않는다.
2. 활성화 시 transient GitHub 인증으로 최신 main SHA와 전체 recursive Git tree를 대조한다.
   commit의 source 목록·각 blob ID가 모두 맞아야 한다. 일반 runner의 보호 규칙은 풀지 않는다.
3. 기존 hold가 없고 역할 선택·source가 준비 시점과 같으며 디스크 예산이 충족될 때만
   소유 hold를 만들고 활성 작업 0을 두 번 확인한다. 분석만 중지하고 TTS는 유지한다.
4. source·schema와 build revision을 교체한다. live supervisord.conf, credential,
   기존 frozen Core/H5와 model 자산, 작업 journal은 보존한다. obsolete tracked source만 제거한다.
5. 새 분석 PID가 소유한 Native/MFA·실행 worker 준비, 정확한 release revision, direct ready를
   확인한 뒤 성공 receipt를 쓴다. 불확실한 작업을 재추론하지 않는다.

성공 snapshot과 receipt는 persistent home에 저장한다. 이후 기동은 원본 source와 frozen
자산을 SHA로 복구하고 실제로 성공했던 revision/bundle receipt를 복원한다. 이전 단계의
runtime overlay가 새 main을 다시 덮어쓰는 복구 경로는 선택하지 않는다. role key는 기존
별도 주입 경로를 계속 사용한다. 전체 container 재생성·Backend admission 활성화·품질
검토를 수행했다는 뜻은 아니다.

실패하면 root transaction의 source·metadata·private env backup으로 원복하고 접수를 닫아
둔다. 운영자가 이전 서비스의 준비를 확인한 뒤 자신이 만든 hold만 해제한다. 백업·token·
payload는 Git에 넣지 않는다. 배포 후 public/local 준비, Sol 선택, 보관·이력 pending과
TTS 프로세스 상태를 운영 조회로 확인한다.
