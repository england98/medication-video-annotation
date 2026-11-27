# 합성 예제

`linkage/frame-map.json`, `linkage/dataset-index.jsonl`은 저장 연결 명세를 설명하는 합성 메타데이터다. 경로에 표시된 영상/프레임은 제공하지 않으며 실제 자료 존재나 관찰 완료를 의미하지 않는다. 테스트는 임시 디렉토리에 합성 바이트를 만들어 해시·저장·경로만 검증한다. 디코딩 검증과 구분한다.

공통 revision 양식은 templates의 하나만 사용한다. 합성 완성 record와 전달 형식은 [5파일 예제](../templates/exports/README.md)에만 둔다. `build_synthetic.py`는 이 예제와 연결 메타데이터만 재생성한다.

[시점 라벨 합성 JSON](event-spot.synthetic.json)은 `medication-event-spot-label-v1`의 행동 구간·횟수 완결·검수 필드를 보여 준다. 예제의 원본 locator와 SHA는 자리표시자다. [시점 라벨 규약](../schemas/event-spot.md)

## 실행 가능한 합성 예제

재생·디코딩·화면·준비 재사용 검사는 공통 `demo` 명령이 만드는 도형 영상 두 개를 사용한다. 프로젝트 루트에서 README의 Python/uv 설치를 마친 뒤 실행한다.

```powershell
$env:PYTHONPATH = 'src'
uv run --locked python -X utf8 -m medication_video_annotation demo --output local/synthetic-demo
uv run --locked pytest -q
```

`build_synthetic.py`는 영상 생성기가 아니다. 새 checkout의 전체 검사에 필요한 자료는 위 `demo`로 준비한다. `tests/test_readiness.py`는 `local/synthetic-demo`의 기존 `DEMO-02/revision-000000.json`을 복사해 불변성·준비 재사용·checkpoint 재개를 검사하며, 원본 합성 자료가 없으면 해당 검사들이 SKIP한다. 다른 output 경로를 쓰면 이 검사의 기본 경로를 충족하지 않는다.

합성 예제를 이미 편집한 작업공간에서는 덮어쓰거나 삭제하지 않는다. 검증할 때는 소스를 별도 폴더에 복사하고 그 안의 빈 `local/synthetic-demo`에 생성한다. 새 프로필의 구조·판단·저장 회귀는 `tests/test_semantic_profile.py`가 별도 임시 합성 기록으로 확인한다. 합성 로그·영상·결과는 Git에 포함하지 않는다.
