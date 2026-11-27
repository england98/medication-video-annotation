# 복약 행동 인식 데이터 작업공간

복약·물마시기와 이와 비슷한 행동을 영상에서 구분하기 위한 라벨을 설계하고 작성했다. 원본과 프레임 시간축을 연결하고, 관찰 근거와 행동 구간을 기록하고, 작성·수정·검수 이력을 영상별 revision으로 남겼다.

**정의한 라벨.** 분석 8항목과 행동 라벨을 함께 저장하는 공통 형식(v0.1, v0.2)과, 행동 종류·구간·경계 확실성 중심으로 줄인 시점 라벨을 정의했다. v0.2는 복약 후보의 판단 기준을 손의 입 접근과 섭취형 동작으로 넓히고 음용 등 대안 행동을 함께 기록한다.


## 읽는 순서

1. [결과 보고서](reports/medication-drinking/project-results.md): 형식별 사용본 수량, 검수 범위와 관찰 한계
2. [데이터 라벨링 방법](docs/data-methods.md)과 [라벨·분석 항목 안내](docs/analysis-label-guide.md): 수집·선정·시간축 연결·작성·검수 절차와 세 형식의 항목 비교
3. [시점 라벨 형식](schemas/event-spot.md), [공통 저장 규약](schemas/README.md), [합성 예제](examples/README.md): 필드, 허용값, 가상 자료
4. [도구 설치와 실행](#도구-설치와-실행): 도형 영상 두 개로 준비·저장·조회 흐름 확인

## 도구 설치와 실행

### 설치

PowerShell에서 이 저장소 루트로 이동한다. Python **3.10.20**과 uv를 사용한다.

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'local/tooling/uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) 'local/tooling/python'
# Python 3.10.20이 없는 경우 uv python install 3.10.20
uv sync --locked
$env:PYTHONPATH = 'src'
uv run --locked python --version
```

`uv.lock`으로 의존성을 고정한다. 필요한 패키지가 캐시에 있으면 `--offline`을 사용할 수 있다. 실제 로컬 설정의 항목은 [설정 예제](configs/project.example.toml)를 참조한다.

### 합성 영상 생성과 검수 화면

같은 PowerShell에서 실행한다. 새 폴더에 도형 영상 두 개, 원본 PTS와 재생 시각의 대응표, 합성 라벨 revision을 생성한다. 기존 합성 기록이 있으면 그대로 읽는다.

```powershell
uv run --locked python -X utf8 -m medication_video_annotation demo --output local/synthetic-demo
uv run --locked python -X utf8 -m medication_video_annotation --data-root local/synthetic-demo serve --run local/synthetic-demo/runs/synthetic-workbench --ids DEMO-01 DEMO-02 --port 8767
```

서버가 출력한 <http://127.0.0.1:8767/>을 연다. 분석과 라벨 탭에서 원본 시각, 근거 프레임, 저장 revision을 확인할 수 있다. 실행 터미널에서 Ctrl+C로 서버를 종료한다. 독립된 합성 쓰기 검사는 `uv run --locked pytest -q`로 실행한다. 합성 자료 생성 전에는 일부 준비 재사용 검사가 SKIP될 수 있다. 파일로 제공하는 정적 예제의 역할은 [합성 예제 안내](examples/README.md)에 있다.

### 로컬 원장 조회

실제 자산이 등록된 원본 작업공간에서는 공통 형식과 시점 형식이 각각 다른 화면을 사용한다.

```powershell
.\review.cmd --all
# 지정 영상만 열기: .\review.cmd --ids VIDEO_ID
# 공통 검수 서버 종료: .\review.cmd stop
.\spot-view.cmd --no-open
# 원본 영상 폴더를 옮겼으면: .\spot-view.cmd --source-root <데이터 수집 루트>
# 시점 라벨 화면 종료: Ctrl+C
```

공통 형식 화면은 기본 포트 8768에서 정본을 읽는다. 로컬 사용자는 검수 범위·검수자와 수정값을 한 revision에 저장할 수 있다. 시점 라벨 화면은 기본 포트 8769에서 읽기 전용으로 동작한다. 이벤트를 선택해 구간을 재생하고, 경계 또는 원본을 읽을 수 없을 때 표시되는 상태를 확인한다. 시점 라벨의 합성 JSON은 문서 예시용으로 제공한다.

프레임 PNG는 원본 영상에서 만든 파생 자료다. 백업 등에서 프레임을 빼 두었다면 공통 형식 화면을 열기 전에 다시 만든다. 다시 만든 각 프레임은 프레임 대응표에 기록된 SHA-256과 일치해야 하며, 이미 있는 파일은 덮어쓰지 않는다.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -X utf8 -m medication_video_annotation --data-root local restore-frames --ids VIDEO_ID
# 전체: --all
```

## 자료 범위

공개 저장소에는 라벨링 방법, 결과 보고서, 형식 규격, 도구 소스, 설정 예제와 합성 자료를 둔다. 실제 영상, 라벨 전문, 원본 대응 목록, 체크포인트, 검수 원장과 개인 설정은 원본 작업공간의 로컬 영역에만 보존한다. 공통 v0.1 형식의 전달 예시는 [5파일 내보내기 예제](templates/exports/README.md)에 있다.
