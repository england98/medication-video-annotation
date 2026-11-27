# 새 행동 의미 DEV 인수본 v0.1

`onc110-behavior-dev-handoff-v0.1`은 데이터 작업공간에서 명시적으로 선택한 검수 revision을 고정하는 전달 규격이다. pipeline과 합의된 공동 계약, 학습 형식, 독립 TEST 정답 또는 실제 추론 연결 완료가 아니다. v0.1 5파일 export와 `read_release`는 그대로 유지하며 새 프로필을 계속 거부한다.

사용자 명시 발행 요청과 실제 전체 CLIP 내용 확인의 근거가 필요하다. 공통 통합 검수 저장으로 REVIEWED가 된 revision만 선택한다. 초안 채택 의견만으로 검수 범위·EXACT 경계·실제 삼킴·독립 검수·평가 자격을 만들어 넣지 않는다. 실제 과거 검토 시각/시간이 없으면 null로 남기고 서버의 저장 시각과 구분한다. 검수 타이머 미사용 상태는 NOT_RUN이며 저장된 검수 상태와 독립적이다.

## 구조와 루트

```text
local/handoffs/<handoff_id>/
  manifest.json
  manifest.sha256
  README.txt
  evidence/user-review.json
  records/<video_id>/<선택 revision-000000.json>
  frame-maps/<video_id>.json
```

선택 수는 사용자가 지정하며 특정 개수에 고정하지 않는다. record/map은 바이트 그대로 복사한다. 분석/라벨 분리 사본·CSV·영상·프레임 복사는 만들지 않는다. `.pending-<uuid>` staging에서 같은 reader로 검증한 후 새 디렉터리로 확정한다. 실패 staging은 진단용으로 남으며 발행 완료가 아니다. 기존 handoff_id는 재사용/덮어쓰기하지 않는다.

- manifest의 snapshot `path`는 **인수본 디렉터리 기준** POSIX 상대경로다.
- `DATA_ROOT`는 소비자가 한 번 바인딩하는 **원래 데이터 작업공간의 local** 루트다. `--data-root`로 전달한다. manifest에 실행용 절대경로를 넣지 않는다.
- `source.locator`, 지도 내부 `frames[].image_path`는 DATA_ROOT 기준이다. 지도 snapshot 폴더 기준으로 해석하면 안 된다.
- `revision_ref`, `frame_map_ref`는 원래 위치의 계보다. reader는 이 위치의 최신 record/map을 읽지 않고 고정 snapshot을 읽는다. dataset-index를 수정/재조회할 필요도 없다.
- 이관된 `frame-map.private.json#samples/<ID>`는 파일 경로가 아닌 불투명 별칭이다. manifest의 `map_alias`와 동봉 `dataset_entry.imported_revision`이 정확히 대응할 때만 사용한다. 공통 validator가 ID·지도 SHA·추출 조건·원래 경로를 검사한다. 임의 별칭이나 ID 불일치는 허용하지 않는다.
- `source_lineage` 안의 과거 절대경로는 로컬 출처 설명일 뿐 실행 경로가 아니다. 공개 문서에 실제 값은 넣지 않는다.

정수 `pts`, 분수 `time_base`, 소수 밀리초 `source_ms`, 비영점 `first_source_ms`와 `player_elapsed_ms`를 보존한다. END는 마지막 포함 프레임이다. FPS로 재계산하거나 정수 ms로 반올림하지 않는다. pipeline의 정수 ms 어댑터와 `source_actor_id`↔runtime `target_track` 매핑은 별도 후속 정책이다.

## manifest 필드표

아래 항목은 모두 필수다. null 허용은 해당 행에만 표시한다. 실행 검증은 `dev_handoff.read_dev_handoff()`와 기존 record/profile validator가 담당하며 별도 JSON Schema를 중복 유지하지 않는다.

| 필드 | 자료형 | 의미/규칙 |
| --- | --- | --- |
| format, version | string | 위 형식 ID, `0.1` 정확 일치 |
| handoff_id, created_at_kst | string | 고유 안정 ID, 시간대가 있는 생성 시각 |
| purpose, profile | string | `DEV`, `onc110-behavior-annotation-v0.2` |
| publisher | object | name/version/module, `source_files_sha256` 파일별 해시와 정렬 파일목록이 아닌 **저장된 순서의 encoded JSON**에 대한 `source_tree_sha256`; 배포 서명이 아님 |
| source_tools | object | authoring/review 각각 도구 경로→코드 SHA256. 출처 설명이며 실행하지 않음 |
| selected_ids, selected_count | string[], integer | 명시 선택 순서, 항목 수; 빈 값/중복/순서 불일치 거부 |
| asset_roots | object | `DATA_ROOT: {binding: CALLER_SUPPLIED, access: READ_ONLY}` |
| time_axis | object | 원본 FrameRef·시각 단위·마지막 포함 END·정수 ms 어댑터 미구현을 선언 |
| policy | object | DEV_EXPOSED, final_ground_truth/evaluation_eligible=false, 데이터 원장 READ_ONLY, NEW_REVISION_AND_NEW_HANDOFF_ID, DATA_SIDE_ONLY_NOT_ADOPTED |
| entries | object[] | 아래 선택 영상 항목 |
| user_evidence, instructions | object | `path:string`, `sha256:string`, `bytes:integer`; 사용자 근거 원문과 README snapshot |

| entries[] 필드 | 자료형 | 의미/규칙 |
| --- | --- | --- |
| video_id | string | 고정 원본 ID |
| dataset_entry | object | 기존 목록 행의 전체 메타데이터. 분류·원본명·계보·등록 이관 매핑 보존 |
| source | object | root_alias=`DATA_ROOT`, locator, 실측 sha256, bytes |
| revision_id, revision_ref | string | 명시 번호 파일, 원래 `records/<ID>/<revision>`; latest 금지 |
| record, frame_map | object | 인수본 내부 path·파일 SHA256·바이트 수 |
| canonical_record_sha256 | string | 기존 `record_hash()`의 정렬 JSON 해시. 파일 SHA와 다르며 혼용 금지 |
| record_format, label_format, profile | string | 원래 v0.1 컨테이너와 새 의미 프로필 |
| frame_map_ref, record_frame_map_ref | string | 실제 원래 지도 locator와 record가 가진 참조(별칭 가능) |
| map_alias | object/null | 이관만 `{alias, target, basis: dataset_entry.imported_revision}`; 나머지 null |
| map_summary | object | video_id/source_sha256/frame_count/width/height/first_source_ms; 지도와 일치 |
| annotation | object | 아래 계보/범위 요약; record와 정확 일치 검사 |
| development_exposure | string | `DEV_EXPOSED`; 독립 평가 자격 아님 |
| groups | object | person/capture/derived 각각 `{status, value, basis}`. status=`METADATA_CODE`/`CANDIDATE`/`UNKNOWN`; UNKNOWN 값은 null. 인물 코드를 실명/실제 동일인 확인으로 확대하지 않음 |

`annotation`의 필수 필드는 source_actor_ids(string[]), observed_range(전체 원본 frame_index 범위), scopes(assessment_id/scope/source_actor_id/range/status 배열), record_pointer(`/label/behavior_annotation`), review, authoring, basis, guideline_ref다. review/authoring/basis/guideline_ref는 원래 블록의 값을 그대로 보존한다. 실제 모델 ID UNCONFIRMED와 raw_output NOT_CAPTURED도 승격하지 않는다. 질문·답·근거·구간/PTS·관계·상충/보조사항의 정본은 snapshot record 하나다.

`evidence/user-review.json`에는 reviewer_ref, user_content_acceptance, user_answer, ordered allowed_ranges(`video_id/start_frame_index/end_frame_index`)가 필요하다. 원문 출처·시각 한계 등 확장 필드는 보존한다. 명시 범위와 검수자 일치를 검사하되 도구가 사용자의 실제 확인 행위를 대신 증명하는 것은 아니다.

`manifest.sha256`은 확정 manifest 바이트 SHA256과 `  manifest.json\n`을 담는 별도 ASCII 파일이다. manifest 자체에 자기 해시를 넣지 않는다. reader에는 별도로 전달받은 고정 SHA도 필수다. 이는 변조 탐지이며 외부 서명/신뢰 인증이 아니다.

## 선택과 실행

선택 입력은 local의 `onc110-behavior-dev-selection-v0.1` JSON이다. 필수 필드:

| 필드 | 자료형/내용 |
| --- | --- |
| format | 위 선택 형식 ID |
| handoff_id | 기존에 없는 고유 ID |
| entries | 순서대로 video_id, revision_id, revision_file_sha256, frame_map_sha256, groups |
| user_evidence | DATA_ROOT 상대 path, sha256, bytes |
| source_tools | 작성/검수 시 확인한 코드 SHA256 맵(authoring, review) |

실제 대상 선택은 별도 요청의 ID·원본 계보·기준 revision/파일 해시와 대조해 확정한다. 이미 적절한 검수 후속본이 있으면 재사용하고 내용이 다른 후속본을 자동 채택하지 않는다. 전용 영상 변환기/스크립트를 만들지 않는다.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m medication_video_annotation.timing create --id setup-dev-handoff-UNIQUE --kind SETUP
.\.venv\Scripts\python.exe -X utf8 -m medication_video_annotation --data-root local dev-handoff-publish --selection local/runs/setup-dev-handoff-UNIQUE/selection.json --run local/runs/setup-dev-handoff-UNIQUE --request EXPLICIT_USER_DEV_HANDOFF
.\.venv\Scripts\python.exe -X utf8 -m medication_video_annotation --data-root local dev-handoff-check --handoff local/handoffs/HANDOFF_ID --manifest-sha256 MANIFEST_SHA256 --expect-ids VIDEO_ID_1 VIDEO_ID_2
```

대문자 자리표시자는 실제 선택값으로 바꾼다. 발행은 별도 SETUP 원장에 TOOL_EXECUTION으로 기록하고 완료된 분석 run을 열지 않는다. 검수 저장은 별도 REVIEW run의 공통 통합 저장을 사용한다. 파일이 고정된 발행은 검수 서버와 병행할 수 있으며 record/map/index를 수정하지 않는다. 재읽기는 데이터/원장에 쓰지 않는다.

```python
from medication_video_annotation.dev_handoff import read_dev_handoff

checked = read_dev_handoff(
    handoff_directory,
    media_root=annotation_local_root,
    expected_manifest_sha256=pinned_manifest_sha256,
    expected_ids=ordered_selected_ids,
)
records = checked['records']
frame_maps = checked['frame_maps']
```

reader는 해시·크기·필수 필드·버전/프로필·개수/중복·원본/지도/record/검수 연결·FrameRef·범위와 기록된 PNG 해시를 검사한다. 절대경로, 드라이브, 역슬래시, `..`, 루트 밖 symlink/junction, 미등록 별칭, 잘못된 ID/해시/검수 상태를 거부한다. 검사 성공은 기존 관찰 내용의 정확도 재평가나 새 원본 관찰이 아니다.

다음 pipeline 작업은 고정 manifest/해시 검사→snapshot 로드→원본 시간과 대상/질문 대응 확인→최소 소비기/왕복 확인이다. 이번 데이터 도구는 pipeline 저장소를 쓰거나 새 E03 추론·학습·성능 평가를 실행하지 않는다. 추가 라벨/수정본은 같은 기능으로 새 선택과 새 handoff_id를 만들고 기존 실험의 고정 입력은 보존한다.
