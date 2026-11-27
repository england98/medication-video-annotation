# 라벨·분석 항목 안내

문서 버전 2.0 · 대상 형식: 공통 v0.1(`onc110-analysis-label-record-v0.1`, label `onc110-video-label-v0.1`), 공통 v0.2(`label.semantic_profile=onc110-behavior-annotation-v0.2`), 시점 라벨(`medication-event-spot-label-v1`)

이 문서는 세 라벨 형식이 무엇을 기록하는지와 각 항목을 어떻게 읽는지 설명한다. 공통 분석 항목과 구간·근거·불확실성의 원칙은 1~3절에서 한 번만 설명하고, 형식별 차이는 4~6절에서 다룬다. 정확한 필드와 허용값은 [공통 저장 규약](../schemas/README.md)과 [시점 라벨 형식](../schemas/event-spot.md)을 따른다. 형식별 수량과 검수 결과는 [결과 보고서](../reports/medication-drinking/project-results.md)에 있다. 본문의 예시는 모두 합성 설명이다.

## 1. 세 형식 비교

| 구분 | 공통 v0.1 | 공통 v0.2 | 시점 라벨 |
| --- | --- | --- | --- |
| 저장 단위 | 영상별 revision 하나에 analysis와 label | v0.1과 같은 revision 구조, `label.behavior_annotation` 확장 블록 | 영상별 revision 하나에 라벨만 |
| 분석 8항목 | 있음 | 있음 | 없음 |
| 행동 종류 | E01 준비 · E02 복약 후보 · E03 음용 | E01 · E02 · E03 | 준비 · 복약형 · 음용 · 종류 불확실 |
| 복약 후보 질문 | 약으로 식별되는가, 입안 전달이 보이는가 | 손의 입 접근이 보이는가, 섭취형 동작이 보이는가 | 질문 없음, 행동 종류로 기록 |
| 대안 행동·관계 | 없음 | 대안 행동, 같은 행동 묶음, 이벤트 간 관계 | 없음 |
| 시간축 | 원본 프레임 번호와 원본 PTS(ms) | 프레임 번호, 원본 정수 PTS, time_base, 원본 ms | 재생 경과시간(ms), `first_source_ms` 함께 기록 |
| 경계 상태 | EXACT · APPROXIMATE · UNCONFIRMED | EXACT · APPROXIMATE · UNCONFIRMED | CLEAR · APPROXIMATE · UNRESOLVED |
| 판단 | 세션 판단(`session_label`) | 범위별 판단(`scope_assessments`) | 영상 판단(`video_judgment`) |
| 검수 기록 | `label.review` (저장 당시 값) | `behavior_annotation.review.human_review` | `review` (저장 당시 값) |
| 정확한 규격 | [저장 규약](../schemas/README.md#revision), [공통 양식](../templates/revision.template.json) | [v0.2 프로필](../schemas/README.md#행동-의미-프로필-onc110-behavior-annotation-v02) | [시점 라벨 형식](../schemas/event-spot.md) |

## 2. 공통 분석 항목 (v0.1, v0.2)

공통 형식은 영상 전반의 관찰을 `analysis.categories`의 8개 범주로 기록한다. 범주마다 `status`와 비어 있지 않은 `observations`가 있다. 관찰 항목의 `basis`는 출처·기술 정보에 근거한 `METADATA`와 실제 프레임에 근거한 `DIRECT_OBSERVATION`으로 나뉜다. `DIRECT_OBSERVATION`에는 근거 프레임 번호가 필요하다.

| 범주 | 기록하는 내용 |
| --- | --- |
| `source_and_duplicates` | 출처, 원본 계보, 중복을 확인한 범위와 확인하지 못한 부분 |
| `people` | 화면 속 인물 수와 클립 안의 추적 단서. 신원·건강 상태는 추정하지 않는다 |
| `capture` | 카메라 위치, 구도, 움직임, 화면 전환 |
| `environment` | 배경과 행동에 관련된 주변 물체 |
| `view_and_visibility` | 손·입·물체의 가시성, 가림, 화면 밖으로 나감 |
| `behavior` | 직접 관찰한 행동 흐름과 확인하지 못한 부분 |
| `technical_quality` | 해상도, 흐림, 작은 화면 점유율, 관찰 매체의 한계 |
| `evaluation_use` | 활용 가능성과 제약에 대한 서술. 사용 허가 표시가 아니다 |

`analysis.observation_log`에는 실제로 화면에 표시해 관찰한 기록을 남긴다. 항목마다 관찰 종류, 관찰자, 기록 시각, 표시 묶음 ID, 포함 프레임 범위, 실제 표시 너비·높이가 있다. 프레임 준비나 디코딩은 관찰 로그를 채우지 않는다. 저장 검사는 로그가 전 프레임을 덮는지 확인하지만, 모든 장면을 정확히 해석했다는 증명은 아니다. 관찰 로그의 겹침은 더하지 않고 합집합으로 센다.

## 3. 구간·근거·불확실성

### 3.1 답변과 빈 값

| 값 | 뜻 | 읽을 때 |
| --- | --- | --- |
| `YES` | 질문을 뒷받침하는 관찰이 있다 | 질문별 긍정으로만 센다 |
| `NO` | 부정 판단을 뒷받침하는 관찰이 있다 | 보이지 않음과 다르다 |
| `UNKNOWN` | 관찰했지만 결정할 근거가 부족하다 | NO와 분리하고 관찰 근거와 한계를 함께 읽는다 |
| `null` | 값이 아직 없거나 적용되지 않는다 | 0이나 NO로 바꾸지 않는다 |
| `[]` | 목록에 항목이 없다 | 이것만으로 이벤트 부재·미작성·관찰 불가를 구분할 수 없다 |

미작성, 이벤트 부재, 관찰 불가는 서로 다르다. 부재는 해당 범위를 관찰했다는 근거와 함께 명시적으로 기록한다. 일부 구간의 부재를 클립 전체나 촬영 범위 밖으로 넓히지 않는다.

### 3.2 구간과 시간

- 프레임 번호는 0부터 시작하고 시작(START)과 끝(END)을 모두 포함한다. END는 마지막 포함 프레임이다.
- 원본 PTS의 첫 값은 0이 아닐 수 있다. 재생 경과시간은 `source_ms − first_source_ms`다. 시점 라벨은 재생 경과시간을, 공통 형식은 원본 PTS를 저장한다.
- 경계 상태는 구간의 시작·끝을 얼마나 확실히 정했는지를 뜻한다. 정확한 경계(EXACT, CLEAR)는 기록된 확인 근거가 있을 때만 쓰고, 대략이면 APPROXIMATE, 정하지 못했으면 UNCONFIRMED 또는 UNRESOLVED를 쓴다. 모르는 쪽 경계는 비워 두고 영상 전체로 넓히지 않는다.
- 영상 시작이나 끝에서 동작이 잘렸으면 공통 형식은 `truncated_start`, `truncated_end`로 표시한다. 시작 시각을 0으로 저장했다고 동작의 실제 시작을 확인한 것은 아니다.
- 행동 횟수는 종류별로 기록한 구간의 수다.

### 3.3 근거와 한계

근거는 판단을 뒷받침하는 실제 관찰 프레임과 그 프레임에서 본 내용이다. 한계는 가림, 작은 크기, 흐림, 화면 밖 이동처럼 판단을 제한한 이유와 그 프레임이다. 손이 입에 다가가는 동작만으로 약 식별이나 복용을 확정하지 않는다. 복약형·섭취형 동작의 관찰은 약 성분, 실제 삼킴, 처방 일치를 확인하지 않는다. 음용 동작도 액체의 종류나 양을 확인하지 않는다. 폴더와 파일명의 행동 코드는 목록 정보이며 근거가 아니다.

### 3.4 상태와 revision

일반 상태는 미작성(`UNANNOTATED`), 초안(`DRAFT`), 검수 기록이 있는 상태(`REVIEWED`)다. 첫 유효본은 `revision-000000.json`이고 실제로 내용이 바뀔 때만 다음 번호를 쓴다. 이전 revision은 보존한다. `record_hash`는 정렬 JSON의 논리 해시이고 revision 파일 SHA-256은 파일 바이트의 해시다. 집계 대상을 고정할 때는 파일 SHA-256을 쓴다. 체크포인트는 미완성 작성 상태이며 유효 revision을 대신하지 않는다.

## 4. 공통 v0.1

v0.1은 분석 8항목과 행동 라벨을 같은 revision에 저장한다. 라벨은 준비·복약 후보·음용의 후보 구간을 표시하고, 구간마다 두 질문에 답과 근거를 남긴다. 후보 구간이 있다고 그 질문이 YES라는 뜻은 아니다.

| 종류 | 질문 키 | 판단 내용 |
| --- | --- | --- |
| E01 준비 | `handling_visible` | 약 관련 포장·용기 등 후보 물체를 다루는 동작이 보이는가 |
| E01 준비 | `contents_removed_visible` | 내용물을 꺼내는 것이 보이는가 |
| E02 복약 후보 | `object_is_medication` | 물체가 약으로 식별되는가 |
| E02 복약 후보 | `object_transfer_into_mouth_visible` | 물체가 입안으로 전달되는 것이 보이는가 |
| E03 음용 | `container_reaches_mouth` | 용기가 입에 닿는가 |
| E03 음용 | `drinking_motion_visible` | 마시는 동작이 보이는가 |

- `label.events[]`에는 이벤트 ID, 종류, 상태, 클립 안의 인물·물체 키, 본 구간(`interval`), 앞뒤 맥락 구간, 질문별 답(`check_answers`), 근거(`evidence`), 한계(`limitations`), 물체·이벤트 연결(`links`)이 있다. 한 영상에 같은 종류의 이벤트가 여러 번 있을 수 있다.
- E01은 약 관련 취급 후보를 다룬다. 일반적인 컵 들기를 E01로 분류하지 않는다. E02의 두 답이 모두 YES이면 같은 물체를 잇는 확인된 연결 근거가 필요하다.
- `reference_verification`은 검수된 답에서만 계산하는 파생 값이며 초안에서는 null이다.
- `label.observations`는 이벤트로 표현하지 않은 부재와 관찰 한계를 범위와 함께 담는다.
- `label.session_label`은 클립 관찰창 또는 연속 세션 범위의 판단이다. 처방·기대 행동·연속 세션 범위가 없는 짧은 클립에서는 적용 불가와 이유를 기록한다.
- `label.review`는 검수자, 검수 시각과 이력을 담는다. 검수 후 내용이 바뀌면 다시 검수 대상이 된다. 사용본 21개의 검수 완료는 사용자의 직접 확인을 근거로 하며 결과 보고서에 기록했다. 파일의 검수 필드는 저장 당시 값을 유지한다.

필드별 형식과 검사 조건은 [공통 양식](../templates/revision.template.json), [저장 규약](../schemas/README.md#revision), 검증 코드 [legacy_record.py](../src/medication_video_annotation/legacy_record.py)와 [records.py](../src/medication_video_annotation/records.py)에 있다.

## 5. 공통 v0.2

v0.2는 v0.1의 저장 구조와 분석 8항목을 그대로 두고 행동 판단 기준을 확장했다. `label.semantic_profile=onc110-behavior-annotation-v0.2`이면 `label.behavior_annotation`에서 답·이벤트·판단을 읽는다. 형식명이 v0.1과 같으므로 프로필 표시 없이 옛 `label.events`를 현재 답으로 읽으면 안 된다.

| 항목 | 내용 |
| --- | --- |
| 질문 | E01 `handling_visible`, `contents_removed_visible`; E02 `hand_to_mouth_visible`, `intake_motion_visible`; E03 `container_reaches_mouth`, `drinking_motion_visible`. 복약 후보는 약 식별 대신 손의 입 접근과 섭취형 동작을 묻는다 |
| 대상 | `actors[].source_actor_id`는 원본 안의 대상이다. 실행 시스템의 추적 ID와 섞지 않는다 |
| 같은 행동 | `same_action_group_id`는 실제 같은 물리적 동작이다. 시간이 가깝다는 이유로 묶지 않는다 |
| 대안 행동 | E02의 `alternative_action`에 음용·식사·얼굴 접촉·기침 등 다른 설명이 있는지(NONE/OBSERVED/UNRESOLVED) 기록한다 |
| 관계 | `relations`에 같은 행동, 독립된 후속 행동, 미해결 관계와 근거를 기록한다 |
| 관측 범위 | `coverage`는 종류별로 PRESENT/ABSENT/NOT_COVERED/UNKNOWN을 구분한다 |
| 판단 범위 | `scope_assessments`는 대상·범위(CLIP 또는 FULL_SESSION)별로 CONFIRMED/NOT_OBSERVED/UNCERTAIN을 기록한다 |
| 시간 | FrameRef(`frame_index`, 정수 `pts`, 분수 `time_base`, `source_ms`)는 지도 값과 정확히 같아야 한다 |

- **CONFIRMED**는 같은 대상의, 판단 범위 안에 완전히 들어오는 E02에서 두 질문이 YES이고 대안 행동이 없으며 이를 막는 관계가 없을 때다. E01이나 E03만으로는 확인하지 않는다.
- **NOT_OBSERVED**는 전 프레임을 관찰했고 근거 이벤트와 미해결 상충이 없을 때다. 촬영 범위 밖의 부재를 뜻하지 않는다.
- **UNCERTAIN**은 판단을 바꿀 수 있는 미해결 상충(`unresolved_conflicts`)이 있거나 근거가 부족할 때다. 판단에 영향이 없는 보조 사항은 `auxiliary_open_issues`에 둔다.
- 같은 행동의 E03가 음용으로 확인되면 그 E02는 복약 근거로 쓰지 않는다. 범위 교차, 잘린 행동, 경쟁 해석의 세부 규칙은 [v0.2 프로필 규약](../schemas/README.md#행동-의미-프로필-onc110-behavior-annotation-v02)을 따른다.
- 기존 v0.1 기록이 있는 영상은 새 기준으로 다시 관찰해 작성했다. `basis.base_revision`에 기준 revision의 번호와 해시를 고정하고, v0.1 부분은 읽기 전용 이력으로 둔다. 옛 관찰 로그·답변·검수 상태를 새 의미로 승계하지 않는다. 새로 등록한 영상은 `base_revision`이 null이다.
- 검수 기록은 `behavior_annotation.review.human_review`의 상태, 검수자, 서버 시각, 확인 범위와 이력이다.
- v0.2 기록은 v0.1의 5파일 내보내기와 release 읽기에서 명시적으로 거부된다. 검수한 revision과 지도를 고정해 전달하는 별도 경로는 [개발용 인수본 규격](dev-handoff.md)에 있다.

## 6. 시점 라벨

시점 라벨은 공통 형식의 행동 종류와 구간 기록 방식에서 출발해 항목을 줄인 형식이다. 행동 종류, 재생 경과시간 기준 시작·종료, 경계의 확실성을 중심으로 기록한다. 상세 분석 8항목, 질문별 답변, 행동 간 관계는 필수 항목에서 뺐다. 기존 공통 라벨을 변환한 것이 아니며, 별도로 선정한 영상 묶음에 적용했다.

| 항목 | 내용 |
| --- | --- |
| 행동 종류 | `PREPARATION` 준비 조작, `MEDICATION_FORM` 복약형 전달, `DRINKING` 음용, `UNCERTAIN` 보이는 행동의 종류를 정하지 못함 |
| 구간 | `start_ms`, `end_ms`(재생 경과시간), `boundary_status`(CLEAR/APPROXIMATE/UNRESOLVED), 짧은 `note` |
| 횟수 | `observed_counts`는 종류별로 기록한 구간 수, `counts_complete`는 영상 전체의 횟수까지 확인했는지 여부 |
| 영상 판단 | `video_judgment`: 보이는 복약형 동작을 확인(CONFIRMED), 충분한 관찰 범위에서 미관찰(NOT_OBSERVED), 그 밖(UNCERTAIN) |
| 관찰 | `observation.mode`와 실제 관찰 방법·범위. 예: `SAMPLED_TARGETED`, `SAMPLED_NATIVE_WITH_LOCAL_FULL_FRAMES`, `EVERY_SOURCE_FRAME_NATIVE` |
| 검수·노출 | `review`(저장 당시 값), `development_exposure` |

- 복약형은 관찰 가능한 행동 분류다. 손이 얼굴 근처에 있다는 사실만으로 붙이지 않고, 다른 설명과 구별할 수 없으면 `UNCERTAIN`으로 둔다.
- `observed_counts`의 0은 기록된 구간이 0개라는 뜻이다.
- 실제 저장본에는 경계 근거 프레임(`boundary_evidence`), 수정 이력(`revision_history`), 원본 프레임·지도 지문과 관찰 로그가 추가될 수 있다.
- 시점 라벨의 검수 완료는 사용자의 직접 확인을 근거로 하며 결과 보고서에 기록했다. `review`와 `counts_complete` 필드는 저장 당시 값을 유지한다.

## 7. 집계와 해석 규칙

- 집계 대상은 영상 ID, 선택한 revision 파일의 SHA-256, 형식·프로필을 고정한 뒤 센다. 고정한 뒤에는 최신본을 자동으로 다시 고르지 않는다.
- 영상 수와 이벤트 수를 구분한다. 한 영상에 같은 종류의 이벤트가 여러 개 있을 수 있다.
- 질문별 답 분포는 해당 종류 이벤트의 그 질문 수를 분모로 한다. null과 누락은 따로 센다.
- 검수 전 기록의 두 질문 YES 조합을 확인이나 정답이라고 부르지 않는다.
- 서로 다른 형식과 프로필의 질문은 바꿔 읽지 않는다. v0.1의 약 식별·입안 전달 답과 v0.2의 손 입 접근·섭취형 동작 답은 다른 질문이다.
- 원본 SHA-256을 다시 계산하지 않았다면 기존 원장 값이라고 밝힌다.
- 클립 판단을 전체 복약 과정의 성공률이나 모델 성능으로 넓히지 않는다. 정답이나 정답을 드러내는 분류·파일명은 모델 입력과 분리한다.

## 8. 변경 기록

- 2.0: 세 형식을 하나의 안내로 정리했다. 공통 분석 항목과 구간·근거·불확실성 원칙을 한 번만 설명하고, 형식별 절로 나눠 비교했다. 필드 정의는 기존 규격으로 연결했다. 라벨 내용이나 판정을 바꾼 기록이 아니다.
- 1.1 (2026-10-27): v0.2 프로필의 질문·범위 판단·검수 구분을 추가했다.
- 1.0 (2026-10-19): v0.1 공통 형식의 항목과 집계 규약을 처음 정리했다.
