# 실제 음성 분석 merge 전 검사

2026-11-05 사용자 지시로 도입했다. `readyz`, 컴파일, JSON 스키마 검사만으로 AI 분석 변경 PR을 merge하지 않는다. `voice-analysis-smoke` 상태가 PR의 현재 head에서 성공해야 한다. 커밋이 추가되면 다시 실행한다.

## 실행

DLPC 운영자는 깨끗한 후보 checkout과 해당 소스로 생성한 불변 H5 패키지를 준비한 뒤 실행한다. 실제 음성은 Git에 추가하지 않으며 `tests/voice/cases.json`의 해시와 원고로 식별한다.

```sh
sudo python3 scripts/run_voice_smoke.py \
  --source /absolute/candidate-checkout \
  --fixtures-root /home/2020067/intelligentAI/tests/voice \
  --release-root /absolute/candidate-release-packages \
  --output /var/lib/intelligentai/voice-smoke/unique-candidate-run
```

후보 코드와 실제 CORE/H5는 서비스 UID로 실행한다. 운영 env에서 필요한 OpenAI 키와 실행 경로만 메모리로 전달한다. 운영 작업을 재실행하지 않고, 테스트 결과를 사용자 이력이나 B2로 발송하지 않는다. 원본 음성과 CORE는 수정하지 않는다. 같은 파일인 `testaudio.m4a`와 `testaudio1.m4a`는 별도 성공 사례로 부풀리지 않고 alias로 기록한다.

direct와 internal 각각의 CORE/H5 조합에서 서로 다른 세 미디어를 분석한다. 실제 MFA·native/GOP·GPT·서버 검산·최종 응답 생성을 수행한다. 각 사례에서 ACCEPT, 유효한 GPT 응답, 숫자 종합점수, 피드백, GPT POST 1회, 원본 CORE 바이트 보존과 GPT 원본 전달 SHA 일치를 요구한다. 테스트용 저장소와 프로세스는 운영 저장소와 분리한다. Backend DB·증빙 업로드·이력 동기화와 브라우저 확인은 이 검사의 범위가 아니다.

루트 소유이고 다른 사용자가 수정할 수 없는 경로의 `receipt.json`만 상태 게시에 사용한다. PR의 head·tree와 receipt가 일치해야 하며 두 조합의 모든 사례가 성공해야 한다.

```sh
# GH_TOKEN은 호출 프로세스에만 주입한다. 파일이나 명령행에 토큰을 넣지 않는다.
python3 scripts/publish_voice_smoke.py --receipt /var/lib/intelligentai/voice-smoke/unique-candidate-run/receipt.json --pr PR_NUMBER
```

GitHub main 보호 규칙의 필수 상태에 `voice-analysis-smoke`를 등록한다. 실패·누락·오래된 head의 결과로 merge하지 않는다. 자격 증명을 가진 자동 실행 환경을 임의 fork PR 코드에 노출하지 않기 위해 운영자가 후보를 검토한 후 실행하는 절차다. 배포 후에는 외부 HTTP를 통한 테스트 음성 분석도 별도로 확인한다.
