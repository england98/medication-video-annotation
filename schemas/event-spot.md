# 시점 라벨 형식

`medication-event-spot-label-v1`은 한 영상에서 관찰한 준비 조작, 복약형 전달, 음용의 구간을 기록한다. [합성 라벨 JSON](../examples/event-spot.synthetic.json), [데이터 라벨링 방법](../docs/data-methods.md), [결과 보고서](../reports/medication-drinking/project-results.md)를 함께 읽는다.

## 영상과 이벤트

| 필드 | 읽는 법 |
| --- | --- |
| `format`, `video_id`, `revision` | 형식명, 원장 영상 ID, 0부터 시작하는 저장 번호 |
| `source` | 데이터 루트 기준 상대 `locator`, 원본 `sha256`, `duration_ms`, 첫 프레임 원본 시각 `first_source_ms` |
| `time_axis` | `PLAYER_ELAPSED_MS`. 첫 표시 프레임을 0으로 한 재생 경과시간 |
| `target_id` | 클립 내부 대상 식별자. 가시성이나 대상 특정이 어려우면 null |
| `events[]` | 이벤트의 `event_id`, `type`, `start_ms`, `end_ms`, `boundary_status`, `note` |
| `observed_counts`, `counts_complete` | 종류별로 기록한 이벤트 수와 전체 횟수 완결 여부 |
| `video_judgment`, `observation` | 영상 판단과 실제 관찰 방식·범위 |
| `review`, `development_exposure` | 기록된 검수 상태와 알려진 개발 노출 |

이벤트 종류는 `PREPARATION`, `MEDICATION_FORM`, `DRINKING`, `UNCERTAIN`이다. 종류가 특정된 행만 해당 종류의 관찰 횟수에 센다. 구간의 알려진 시각은 영상 재생 범위 안에 있다. 양쪽 경계가 알려졌으면 시작이 종료보다 이르다. 끝은 마지막 포함 프레임이다. 경계가 불확실하면 `UNRESOLVED`로 기록하고 모르는 쪽을 null로 남길 수 있다. `APPROXIMATE`와 `CLEAR`는 경계에 관한 값이다. `CONFIRMED`는 보이는 복약형 동작에 관한 영상 판단이다. `NOT_OBSERVED`는 부재를 확인할 수 있는 관찰 범위에서 사용하고, 그 판단이 어려우면 `UNCERTAIN`을 사용한다.

프레임 지도의 원본 시각과 이벤트의 재생 경과시간을 연결할 때는 `first_source_ms`를 반영한다. 실제 저장본에 따라 `boundary_evidence`, `revision_history`, 원본 프레임·지도 지문과 관찰 로그가 추가된다. `observation.mode`의 예로 `SAMPLED_TARGETED`, `SAMPLED_NATIVE_WITH_LOCAL_FULL_FRAMES`, `EVERY_SOURCE_FRAME_NATIVE`가 있다. 저장본의 관찰 범위를 읽어 어느 프레임에서 판정했는지 확인한다.

## 상태와 사용 범위

`review`와 `counts_complete` 필드는 저장 당시 값을 유지한다. 사용본 389개의 저장 값(`status=AI_DRAFT`, `human_review=NOT_RUN`, `counts_complete=false` 등)은 검수 뒤에 고치지 않았으며, 389개 전수 검수 완료는 사용자의 직접 확인을 근거로 [결과 보고서](../reports/medication-drinking/project-results.md)에 기록했다. `observed_counts`가 0인 종류는 관찰 기록에 해당 행이 0개였다는 뜻이다. `NOT_KNOWN_EXPOSED`는 알려진 노출 이력의 범위를 나타낸다.

시점 라벨은 [읽기 전용 화면](../README.md#로컬-원장-조회)에서 연다. 공통 형식의 검수 편집이나 전달은 [공통 저장 규약](README.md)을 따른다.
