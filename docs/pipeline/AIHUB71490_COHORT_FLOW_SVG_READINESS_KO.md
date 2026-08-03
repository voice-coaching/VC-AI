# AI Hub 71490 cohort-flow SVG 재현성·발행 준비도

- 기준일: 2026-07-29 (Asia/Seoul)
- 범위: 집계 전용 AI Hub 71490 cohort-flow SVG의 검증·발행 경계
- 판정: **PASS — canonical 재발행, deep 검증, 반복 결정성 검증 완료**
- 연구 상태: 자동 G0 `passed`, 외부 전문가 정렬 G0 `PENDING`

이 판정은 그림 발행기의 재현성과 안전 경계에만 적용된다. locked-test를
열었다는 뜻이 아니며, 외부 전문가 정렬 감사나 P04/P05 또는 연구 전체
완료를 주장하지 않는다.

## 1. 고정 namespace와 구현 provenance

발행기는 다음 경로만 canonical namespace로 허용한다.

- config:
  `configs/paper/aihub71490_cohort_flow_v1.yaml`
- editable source:
  `docs/paper/figures/aihub71490_cohort_flow.mmd`
- output:
  `docs/paper/figures/aihub71490_cohort_flow.svg`
- receipt:
  `artifacts/reproducibility/paper_figures/aihub71490_cohort_flow_v1.json`
- lock:
  `artifacts/reproducibility/paper_figures/.aihub71490_cohort_flow_v1.lock`
- renderer:
  `src/voice_coach/reproducibility/cohort_flow_svg.py`
- CLI:
  `scripts/render_aihub71490_cohort_flow.py`

config schema와 receipt schema는 각각
`voice_coach.paper.cohort_flow_svg_config.v2`,
`voice_coach.paper.cohort_flow_svg_receipt.v2`다. config는 실행할 renderer와
CLI의 SHA-256을 고정한다. renderer는 import 시점 bytes, CLI는 renderer를
import하기 전 시작 시점 bytes를 snapshot으로 잡으며, config pin과 현재
pathname identity가 모두 일치할 때만 발행한다. 테스트 fixture에서만
명시적인 isolated implementation snapshot을 허용한다.

| 대상 | SHA-256 | bytes |
|---|---|---:|
| config | `ffeb6d0e4ffc7002283a8e3c355c59f3b58a5b3f8e9007472f8f03df8e359029` | 1,682 |
| renderer | `cc9ea419740c2acb39b10510d5d48ce3fd6853aaced30da6b7c95836f6e1ed62` | 76,334 |
| CLI | `f6052c05e880a75fa1ba14ec169806895b6ec431ea55ca4571ce6e97c65d1b7d` | 6,131 |
| unit test | `75c1f6d632fb97acd99d4457c0dcd25c97390c834596dc7689d31a8cf1c617cd` | 28,313 |

## 2. 입력 검증 경계

모든 config/source/code read는 parent symlink를 거부하고
`lstat-before → O_NOFOLLOW open → fstat-before → exact-size read →
fstat-after → lstat-after`를 확인한다. 비교 identity는
device, inode, mode, nlink, size, mtime_ns, ctime_ns를 포함한다.
regular file, `nlink=1`, workspace 내부 경로만 허용하며 발행 직전과
post-publish deep 검증 종료 직전에 snapshot을 다시 검증한다.

Mermaid와 네 JSON source의 SHA-256은 config와 Mermaid lineage 주석에서
동시에 고정된다. JSON/YAML duplicate key, JSON non-finite number, XML 1.0
금지 문자, script·`foreignObject`·외부 URI를 fail-closed로 거부한다.

집계 산술은 source label만 비교하지 않고 다음을 다시 계산한다.

- data card, G0, prompt reservation, filter report의 전체·split row 수
- data card와 G0의 split assignment, speaker/PCM/QC/phone-operation 집계
- G0 고정 threshold와 pairing/decoder/time-bound/parse/overlap gate
- modeling manifest와 prompt reservation의 교차-source SHA lineage
- prompt disposition 및 split별 reservation 합계와 filter 제외 상·하한
- filter의 순차 제외 합, train/dev 합, output row 수·role·phone-disabled 상태

## 3. 단일 작성자와 pair transaction

발행은 workspace-root directory에 대한 공통 advisory lock을 먼저 잡고,
canonical `0600`, `nlink=1`, 빈 regular lock file의 `flock(LOCK_EX)`를
추가로 유지한다. 이 이중 경계는 잠긴 output-root를 rename한 뒤 같은
canonical pathname에 새 디렉터리와 새 lock을 만드는 inode rebind에서도
새 작성자가 별도 lock domain으로 진입하지 못하게 한다. lock pathname
identity는 각 write 전후와 deep 검증에서 재확인한다.

SVG와 receipt는 각각 같은 디렉터리의 임시 파일에 write·fsync한 뒤
`os.replace`하고 parent directory를 fsync한다. 최종 mode와 link 수는
SVG `0644/1`, receipt `0440/1`, lock `0600/1`이다. 두 번째 write 또는
post-publish deep 검증이 실패하면 기존 pair가 완전했던 경우 두 파일을
복원하고, 기존 pair가 불완전하거나 없었던 경우 두 canonical output을
모두 제거한다.

## 4. 회귀·공격 테스트

실행:

```bash
PYTHONPATH=src .venv-scorer/bin/pytest -q tests/unit/test_cohort_flow_svg.py
```

결과: **34 passed**.

검증 범위는 다음을 포함한다.

- 5개 source × 2개 output의 10개 lexical alias 조합
- source/output symlink와 hardlink, output/receipt 잘못된 mode·nlink
- source pathname inode swap과 stale renderer snapshot
- 동시 작성자 직렬화 및 output-root directory inode rebind
- 두 번째 write 실패의 신규 pair 제거와 기존 pair 복원
- post-publish deep 실패 rollback
- G0 metric/threshold 변조와 data-card/G0/prompt/filter lineage·count 변조
- duplicate YAML/JSON key, XML control character, 외부 URI
- 결정적 재발행, XML parse, 선택적 LibreOffice PDF 변환

정의된 위협 행렬의 잔여 판정은 **Critical 0 / High 0 / Medium 0**이다.
이 숫자는 위 테스트가 다루는 cohort-flow renderer 경계에만 한정된다.

정적 검사:

```bash
.venv-scorer/bin/ruff check \
  src/voice_coach/reproducibility/cohort_flow_svg.py \
  scripts/render_aihub71490_cohort_flow.py \
  tests/unit/test_cohort_flow_svg.py

PYTHONPATH=src .venv-scorer/bin/mypy --ignore-missing-imports \
  src/voice_coach/reproducibility/cohort_flow_svg.py \
  scripts/render_aihub71490_cohort_flow.py \
  tests/unit/test_cohort_flow_svg.py
```

결과는 Ruff 오류 0, mypy 오류 0이다.

## 5. canonical 발행 결과

실행 순서:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/render_aihub71490_cohort_flow.py --config-only
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/render_aihub71490_cohort_flow.py
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/render_aihub71490_cohort_flow.py --validate-only
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/render_aihub71490_cohort_flow.py
```

네 단계는 모두 `passed` 또는 `config_valid`로 종료했다. 마지막 두 발행의
SVG와 receipt SHA-256은 각각 완전히 같았다.

| 산출물 | SHA-256 | bytes | mode | nlink |
|---|---|---:|---:|---:|
| SVG | `d697e42cea9a820c24c082dd183653bc0a59a9ba4e3406c85eafb8117437ce9e` | 13,542 | `0644` | 1 |
| receipt | `4809b501e09c8f374e4a27760fea9a6e35f76f7d173b9eb8490b85f586bc7eff` | 2,907 | `0440` | 1 |
| lock | 빈 파일 | 0 | `0600` | 1 |

SVG는 16 nodes, 15 edges, script 0, external reference 0,
`foreignObject` 0이다. receipt에는 row identifier 0, speaker identifier 0,
raw/restricted read 0, `locked_test_opened=false`가 기록돼 있다.

## 6. PDF portability smoke

canonical SVG를 LibreOffice headless로 임시 PDF에 변환해 확인했다.

- 변환 exit code: 0
- pages: 1
- JavaScript: no
- encrypted: no
- embedded fonts:
  `NotoSansCJKkr-Bold`, `NotoSansCJKkr-Regular`
- Ghostscript null-page validation: pass
- `/JavaScript`, `/JS`, `/Launch`, `/EmbeddedFile` token: 0
- 120 DPI page render 시 겹침·잘림: 0
- 오른쪽 dashed G→H label `대체하지 않음`: `rotate` 없이 수평,
  우측 여백 내부에서 connector 및 node와 간섭 없이 판독 가능

임시 PDF는 검증 후 삭제했으며 repository 산출물로 승격하지 않았다.

## 7. 남은 작업

1. standalone PDF 시각 감사는 통과했다. 최종 논문 PDF 조립 후에는
   조판 과정의 추가 축소·crop 여부만 다시 확인한다.
2. 외부 전문가 정렬 G0가 실제 완료될 때만 source, Mermaid label,
   config pin, SVG, receipt를 새 버전으로 함께 갱신한다.
3. 이 renderer 준비도만으로 P04/P05나 전체 연구 상태를 완료로 바꾸지
   않는다.
