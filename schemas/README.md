# 저장 규약 v0.1

공통 양식 원본은 [revision.template.json](../templates/revision.template.json) **하나**다. 필드 검증은 `src/medication_video_annotation/legacy_record.py`, 연결/저장 검증은 `records.py`가 실행한다. 별도 JSON Schema를 중복 유지하지 않는다. 기존 함수의 인수 근거는 [reuse-provenance.json](../docs/reuse-provenance.json)에 있다.

## Revision

기존 `onc110-analysis-label-record-v0.1`의 `format`, `video_id`, `analysis`, `label`, `measurements`, `revision_history`를 유지한다. label은 `onc110-video-label-v0.1`이다. 분석 8항목·관찰 로그·coverage·events·session_label·review와 확장 필드를 보존한다. `{{video_id}}`, `{{source_sha256}}`, `{{frame_map_ref}}`는 메모리에서 채우는 자리표시자다. 양식을 그대로 저장할 수 없다.

- `instantiate()`는 메모리 객체만 반환하며 파일/영상별 폴더를 생성하지 않는다.
- 첫 저장은 실제 전프레임 관찰, 8항목 작성, E01/E02/E03 coverage, 이벤트별 답변/근거 검증을 통과한 초안이다. 진행 중인 미완성 관찰은 checkpoint 도구에서 관리한다.
- `events=[]`에는 관찰 부재/한계와 실제 관찰 범위가 필요하다. UNKNOWN/null/NO를 바꾸거나 결과를 자동 완성하지 않는다.
- `save_revision(DATA_ROOT, item, frame_map, record, base_hash=..., actor=...)`는 첫 초안을 `records/<video_id>/revision-000000.json`에 저장한다. 실제 내용 변경 시만 다음 번호를 만든다. 기존 파일은 덮어쓰지 않는다.
- 조회한 전체 record와 `base_hash`를 제출한다. 첫 저장은 `base_hash=None`, 이후에는 반환된 `base_hash`를 쓴다. 기존 해시와 다르면 재조회해야 한다. 기록자와 기존 이력이 필요하며 클라이언트가 이력을 교체할 수 없다.
- `measurements`/`revision_history`를 제외한 전체 record가 동일하면 `UNCHANGED`와 기존 record/경로를 반환한다. 시간만 추가한 제출은 저장하지 않는다. 내용 변경 시에도 이전 measurements를 보존하고 상세 새 시간은 run에만 기록한다.
- 원본·지도·형식을 검사한 뒤에만 records 폴더를 만든다. 작성 중 파일은 임시 파일에 fsync 후 게시한다. Windows에서는 대상이 존재하면 실패하는 rename, POSIX에서는 exclusive link를 사용한다. 번호 누락·동시 작성 lock·충돌 시 자동 덮어쓰기를 하지 않는다.
- 화면의 단일 저장은 수정 내용과 명시 선택한 검수 상태·검수자를 하나의 revision에 함께 기록한다. REVIEWED 선택에는 검수자와 전체 확인 범위가 필요하며 서버 시각/검수 이력을 남긴다. EXACT는 이 경로에서 이벤트별 편집값을 보존하고, 초안으로 저장하면 기존 확정 경계를 해제한다. 같은 내용·상태·검수자는 무변경 처리한다. 타이머는 선택 사항이며 저장/검수 상태와 독립적이다. author/구형 API에서 검수 선택 없이 저장하면 기존 초안/재검토 규칙을 유지한다. 세부 동작은 [실행 안내](../README.md#로컬-원장-조회)를 따른다.

`record_hash`는 이전 코드와 같은 정렬 JSON의 SHA256(낙관적 충돌 검사/previous_sha256)이다. `revision_file_sha256`는 디스크의 정확한 바이트 SHA256이다. 둘은 다른 값일 수 있으며 용도를 혼용하지 않는다.

공통 `author` 세션은 `instantiate`→메모리 값 작성→`save_checkpoint`→`normalize_edit`/`validate_draft`/`save_revision`을 연결한다. 첫 저장도 같은 양식과 검사를 사용하고 영상별 빈 revision이나 입력 JSON 사본을 만들지 않는다. `revision_history`는 서버 소유이며 첫 초안에서는 빈 배열, 후속 작성에서는 조회값을 보존한다. checkpoint는 최신 하나만 갱신하고 stale이면 자동 복원하지 않는다.

`DRAFT_EFFICIENCY`는 run의 완료 범위이며 revision 필드가 아니다. 초안 효율성 시험 종료로 analysis/label을 REVIEWED로 바꾸지 않고 `final_ground_truth=false`, `evaluation_eligible=false`, `independent_review=NOT_RUN`을 유지한다. 시간과 종료 범위는 기존 runs 원장에만 기록한다.

## dataset-index.jsonl → frame-map.json → revision

모든 경로는 **DATA_ROOT(이 프로젝트 local) 기준** POSIX 상대경로다. 현재 작업 디렉토리나 revision 폴더를 기준으로 해석하지 않는다. 절대경로·`..`·역슬래시·드라이브 지정과 루트 밖 symlink/junction을 거부한다. 기존 원본 절대경로는 로컬 `source_lineage`의 출처 정보로만 보관하고 실행 경로로 사용하지 않는다.

dataset-index의 한 줄은 아래 필드를 갖는다. 합성 예시는 [dataset-index.jsonl](../examples/linkage/dataset-index.jsonl)이다.

| 필드 | 연결/의미 |
| --- | --- |
| format | `onc110-dataset-index-entry-v0.1` |
| video_id | 행동명과 무관한 고정 ID; 기존 ID와 충돌 금지 |
| source_sha256 | 원본 영상 바이트의 소문자 SHA256 |
| original_filename | 확장자 포함 원본 영상명 그대로 |
| classification | `tier`, `behavior`: 기존 분류단계/행동코드_행동명 폴더 그대로 |
| source_path | `data/<tier>/<behavior>/videos/<original_filename>` |
| frame_map_ref | `data/<tier>/<behavior>/frames/<원본 stem>/frame-map.json` |
| record_dir | `records/<video_id>`; 경로 참조이며 존재/생성을 요구하지 않음 |
| source_lineage | 원래 위치·출처·원본계보·별칭·동일성 확인 범위/미확인. 과거 분석 판단은 넣지 않음 |

ID·source_sha256은 목록에서 중복되지 않는다. 같은 원본의 다른 위치는 aliases/계보로 남긴다. 원본/지도/record 경로의 대소문자 무시 충돌도 거부한다. 같은 원본 stem 충돌은 파일명 변경이나 덮어쓰기로 숨기지 말고 목록 구성 단계에서 해결한다. dataset-index는 위치/그룹 목록이며 analysis/label 본문이나 최신 revision 사본을 포함하지 않는다.

[frame-map 합성 예시](../examples/linkage/frame-map.json)의 필수 필드:

| 필드 | 의미 |
| --- | --- |
| format | `onc110-frame-map-v0.1` |
| video_id, source_sha256, source_path | 같은 dataset-index 행과 정확히 일치 |
| width, height, frame_count | 원본 픽셀 크기, 총 프레임 수 |
| first_source_ms | 첫 프레임 원본 PTS; 0이라고 가정하지 않음 |
| generation | sampling=`EVERY_SOURCE_FRAME`, resize=false, decoder_version, tool_version |
| frames[].frame_index | 0부터 연속인 원본 프레임 번호 |
| frames[].source_ms | 원본 PTS 밀리초. 유한값·증가 순서 검사, 중복/역행은 검토 필요 오류 |
| frames[].player_elapsed_ms | source_ms − first_source_ms |
| frames[].image_path | 지도와 같은 폴더의 `frame_<6자리 번호>.png`; DATA_ROOT 상대경로 |

revision의 video_id/label.video_id/source_sha256/frame_map_ref는 같은 행과 지도를 참조한다. 이벤트/근거의 ms는 해당 frame_index의 원본 PTS와 일치해야 한다. END는 마지막 포함 프레임이다. 지도 생성은 관찰 로그를 채우지 않는다. 저장은 원본 해시, 지도 내용, 프레임 파일 존재까지 검사한다. 준비 도구와 서버 시작은 PNG SHA256·원본 크기·PNG 형식까지 검증한다. 실제 추출 지도에는 pts/time_base/image_sha256도 보존한다.

실제 파일 이동 후 기존 revision/과거 목록을 고치지 않는다. 새 위치는 현행 목록/이관 매핑으로 연결한다. 사용 데이터가 고정될 때 release에 원본 및 지도·선택 revision의 파일 해시를 고정하는 처리는 후속 단계다.

### 기존 pilot 초안의 바이트 보존 이관

명시적으로 지정된 완성 revision 한 개를 가져올 때는 원래 번호/파일명/본문을 유지한다. dataset-index의 `imported_revision`은 기준 revision의 파일명·SHA256, 기존 `frame_map_ref` 별칭, 새 지도 SHA256, 기존 추출 조건을 고정한다. 옛 `frame-map.private.json#samples/<ID>`는 파일 접근 경로가 아닌 별칭으로만 해석한다. 실제 파일 접근은 새 local 상대경로를 사용한다.

기준 revision 이전 번호는 이관하지 않는다. 공통 읽기/저장은 기준 파일 해시와 기준 번호부터 이어지는 연속성을 검사한다. 무변경 저장은 UNCHANGED이며, 나중에 실제 수정할 때만 마지막 번호+1로 저장한다. 기준 파일 변조·번호 누락·지도 변조는 거부한다. 신규 작성의 000000 시작 규칙은 그대로다.

기존 PNG의 `frame-000000.png` 이름과 바이트를 유지한다. 지도는 기존 PTS·PNG 해시·입력 목록으로 경로만 연결하며, 추출기 버전 미기록은 `NOT_RECORDED_IN_SOURCE_MANIFEST`로 명시한다. 현재 추출기로 다시 만든 것으로 기록하지 않는다. 이관 영상은 원본 파일을 직접 제공하고 근거 이동 시 원본 PTS 시작 오프셋을 적용한다. 별도 재생 사본·이관용 revision·exports를 만들지 않는다.

## 행동 의미 프로필 `onc110-behavior-annotation-v0.2`

v0.1 기록의 의미는 바꾸지 않는다. 새 행동 의미는 같은 revision 컨테이너(`onc110-analysis-label-record-v0.1`, label `onc110-video-label-v0.1`) 안에서 **`label.semantic_profile`과 버전 있는 확장 블록 `label.behavior_annotation`**으로 명시한다. 두 키가 없으면 v0.1, 둘 중 하나만 있거나 프로필 값이 지원 목록 밖이거나 블록의 `profile`이 다르면 거부한다. 검사·연결 코드는 `src/medication_video_annotation/behavior_profile.py`다. 공통 양식 파일은 여전히 하나이며, 확장 블록의 빈 메모리 골격은 `blank_annotation()`/`begin_profile()`이 만든다(영상별 빈 양식 파일 없음).

결정 이유: record/label 형식명을 올리면 기존 v0.1 이력(events/check_answers/session_label/review)까지 새 형식으로 다시 써야 하고, 재사용한 v0.1 검사기(업스트림 AST 고정)를 바꿔야 한다. 확장 블록은 기존 컨테이너·원본/지도 연결·불변 revision을 그대로 쓰면서 새 의미만 분리한다. 대신 v0.1만 이해하는 소비자가 확장을 모르고 옛 events를 읽을 위험이 있으므로 이 작업공간의 내보내기·release reader는 프로필 기록을 명시 거부한다.

| 위치 | 내용·규칙 |
| --- | --- |
| label의 v0.1 부분 | 신규 영상: 빈 양식 그대로(옛 질문 답 없음). 기존 영상: 기준 revision의 값과 바이트 의미가 같아야 하는 읽기 전용 이력. 옛 REVIEWED를 새 의미로 승계하지 않음 |
| basis | `base_revision`(기존 영상: revision_id·record_sha256·revision_file_sha256, 신규: null), `change_reason`. 첫 프로필 revision 이후 고정 |
| guideline_ref | labeling-reference 이름·SHA256 |
| authoring | author_kind(AI_MODEL/HUMAN), author_ref, model_identifier{value, confirmation}, prompt{prompt_id, sha256}, input_set_ids, raw_output{status, ref, sha256, reason}. 모델 ID 값은 CONFIRMED_BY_RUNTIME일 때만, 원문은 CAPTURED일 때 `runs/` 아래 실제 파일·해시가 일치해야 함. 미확인은 null/UNCONFIRMED·NOT_CAPTURED와 이유 |
| observation_log | 새 의미의 자체 관찰 로그(analysis.observation_log와 같은 항목 규칙). 저장본은 전프레임, 기준 revision의 옛 관찰 로그 항목 재사용 거부 |
| actors | source_actor_id(원본 내 대상)·description, runtime_track_links(stream/track/근거/이유, CONFIRMED는 사람 확인 후). actor·event·scope에 runtime track ID 직접 기록 금지 |
| events | event_id, E01/E02/E03, source_actor_id, same_action_group_id, object_id, interval{start, end_last_included(FrameRef), boundary_status, truncated_*}, key_moments, questions, E02의 alternative_action |
| questions | E01 handling_visible/contents_removed_visible, E02 hand_to_mouth_visible/intake_motion_visible, E03 container_reaches_mouth/drinking_motion_visible. 답 YES/NO/UNKNOWN(저장본) 또는 null(checkpoint만), 근거 FrameRef·reason, UNKNOWN은 limitation. 옛 E02 질문 키 거부 |
| alternative_action | status NONE/OBSERVED/UNRESOLVED, type(DRINKING/EATING/FACE_TOUCH/COUGH_OR_SNEEZE/APPROACH_ABORTED/OTHER, NONE이면 null), 근거·이유 |
| relations | SAME_ACTION(같은 묶음일 때만, 같은 묶음 E02·E03에는 필수)/INDEPENDENT_FOLLOWUP(다른 묶음, 뒤 이벤트 시작 > 앞 이벤트 끝)/UNRESOLVED, 같은 대상, 근거·이유 |
| coverage | E01/E02/E03별 PRESENT/ABSENT/NOT_COVERED/UNKNOWN/NOT_EVALUATED. PRESENT⇔이벤트 존재, ABSENT는 전프레임 관찰+근거, NOT_EVALUATED는 checkpoint만 |
| scope_assessments | assessment_id, CLIP/FULL_SESSION, source_actor_id, range, status CONFIRMED/NOT_OBSERVED/UNCERTAIN/NOT_EVALUATED, basis_event_ids, unresolved_conflicts(이 범위 복약 판단의 미해결 상충), auxiliary_open_issues(판단을 바꾸지 않는 보조 미해결 사항, 빈 목록 가능), reason, session_boundaries(FULL_SESSION만), claims_actual_swallowing_or_prescription_match=false |
| review | ai_review(NOT_RUN/COMPLETE), human_review(NOT_RUN/REVIEWED·검수자·서버 시각·전체 범위·이력), independent_review/release_freeze=NOT_RUN. final_ground_truth/evaluation_eligible=false |

FrameRef는 `{frame_index, pts, time_base, source_ms}`이며 지도 프레임의 원본 정수 pts·분수 time_base·source_ms와 정확히 같아야 한다(반올림·frame/fps 재계산 거부). 프로필 기록에는 지도에 pts/time_base가 있어야 하고 `pts×time_base×1000`과 source_ms가 일치해야 한다. END는 마지막 포함 프레임이다. pipeline 정수 ms 대응은 별도 어댑터 정책 후속이며 이번에 외부 계약을 바꾸지 않았다.

판단 규칙(2026-10-27 R1·R2·P1 보완, 근거 run `setup-20261027-182109-profile-rule-fixes`):

- **판단 범위와 교차**: 범위 `[scope_start, scope_end]`와 이벤트 `[event_start, event_end]`는 같은 원본 시간축(frame_index, END=마지막 포함 프레임)에서 `event_start <= scope_end AND event_end >= scope_start`이면 교차한다. 부재·상충 판단(NOT_OBSERVED의 E02 후보 검사, FULL_SESSION의 잘린 행동 검사)은 같은 대상의 교차 이벤트를 모두 본다. 범위 앞에서 시작해 안으로 이어지거나, 안에서 시작해 뒤로 이어지거나, 범위를 감싸거나, 끝점만 맞닿는 이벤트도 교차다. 다른 대상의 이벤트와 교차하지 않는 이벤트(인접 포함)는 대상이 아니다. 검사기는 이벤트 경계·PTS를 범위에 맞춰 자르지 않는다.
- **근거 이벤트**: CONFIRMED의 basis는 같은 대상의 범위 안에 완전히 포함된 충분한 E02만 허용한다(교차 검사와 별개로 유지). UNCERTAIN이 인용하는 근거 이벤트는 같은 대상이며 범위와 교차하면 된다. NOT_OBSERVED·NOT_EVALUATED에는 근거 이벤트가 없다.
- **미해결 상충과 보조 사항**: `unresolved_conflicts`는 이 범위의 복약 판단을 바꿀 수 있는 미해결 상충이다. 하나라도 있으면 CONFIRMED·NOT_OBSERVED·NOT_EVALUATED는 거부하고 UNCERTAIN만 허용한다. UNCERTAIN에서는 상충·근거 이벤트·이유가 그대로 저장·재읽기된다. 판단을 바꾸지 않는 보조 문제(예: E02로 결론이 난 범위의 E01 포장 종류 판독 불가)는 `auxiliary_open_issues`에 두며 모든 상태에서 허용된다. 둘 중 어디에 둘지는 작성자가 정하고, 검사기는 문장을 해석해 옮기거나 고치지 않으며 모순된 저장만 거부한다.
- **같은 행동 묶음과 음용 근거**:
  1. `same_action_group_id`는 실제 같은 물리적 동작이다. 시간 근접이나 전체 복약 과정(episode)만으로 같은 묶음에 넣지 않는다. 같은 묶음의 E02·E03에는 SAME_ACTION 관계가 필요하다.
  2. E03 후보가 있거나 묶음 값이 같다는 것은 음용 검증이 아니다. E03의 음용 판정은 그 E03의 질문 답으로만 정한다: 두 질문 YES는 음용 확인, 두 질문 NO는 기각, 그 밖(UNKNOWN 포함, YES/NO)은 미해결.
  3. 같은 행동의 E03가 음용 확인이면 그 행동은 음용으로 설명된다. E02 대안은 DRINKING(OBSERVED/UNRESOLVED)이어야 하고 그 E02는 복약 판단 근거로 채택하지 않는다.
  4. 같은 행동의 E03가 미해결이면 E02 대안을 NONE으로 둘 수 없다(모순 저장 거부). 이 E02는 근거가 될 수 없으며 범위 판단은 UNCERTAIN을 유지한다.
  5. 같은 행동의 E03가 NO/NO로 기각됐고 E02가 충분하면 E03의 존재만으로 E02 확인을 막지 않는다.
  6. 겹치는 다른 묶음 E03에는 UNRESOLVED 관계가 필요하다. 그 E03가 기각되지 않았으면 E02는 근거가 될 수 없다(UNCERTAIN). 기각된 E03와의 미해결 관계는 막지 않는다.
  7. 복약 뒤 독립 음용(INDEPENDENT_FOLLOWUP)은 충분한 E02를 기각하지 않는다.
  8. 보조 E01과의 SAME_ACTION/UNRESOLVED 관계는 E02를 확인하지도 기각하지도 않는다. E01·E03만으로는 확인할 수 없다.
  9. 다른 E02와의 SAME_ACTION/UNRESOLVED 관계는 같은 동작의 경쟁 해석이므로 기존처럼 근거에서 제외한다.
- **충분한 E02**: 두 질문 YES가 근거이고, 대안 NONE과 위 차단 관계 없음은 전제 조건이다. `alternative_action=NONE` 자체는 복약 성공 증거가 아니다.
- **NOT_OBSERVED**: 전프레임 관찰, 근거 없음, 미해결 상충 없음이 필요하다. 같은 대상의 교차 E02가 없으면 E02 coverage ABSENT, 있으면 모두 섭취형 NO·UNKNOWN 없음·대안 OBSERVED여야 한다.
- **잘림·세션**: 잘린 E02는 잘린 쪽(접근/이탈) 질문을 YES로 둘 수 없다. 범위와 교차하는 잘린 행동이 있으면 FULL_SESSION이 될 수 없다. FULL_SESSION은 시작·끝 근거가 필요하다.

저장: `validate_draft`는 v0.1 검사(공통 원본·분석8항목·전프레임)를 그대로 실행한 뒤 프로필 검사를 추가한다. v0.1 coverage/질문 요구는 프로필 기록에 적용하지 않으며 v0.1 검사 자체는 완화하지 않았다. 프로필 시작 revision의 이력 작업명은 `SEMANTIC_PROFILE_START`다. 프로필을 제거하는 하향 저장은 거부한다. 분석 상태는 새 의미 사람 확인 결과를 따르며 기존 REVIEWED를 이어받지 않는다. checkpoint는 `onc110-checkpoint-v0.2`로 semantic_profile·base_revision_id·observation_progress를 함께 남기고, 다른 프로필의 checkpoint 교체에는 이유가 필요하다.

미지원(명시 거부): 새 프로필의 5파일 내보내기(`bundle_bytes`/`export_selected`)와 v0.1 `read_release`. 새 CSV·기존 release 계약으로 의미를 축소하지 않는다.

별도 지원: [데이터 측 DEV 인수본 v0.1](../docs/dev-handoff.md). `dev_handoff.py`가 명시적으로 선택한 검수 revision과 지도의 원본 바이트를 staging에서 검사한 뒤 고정한다. `validate_link(..., map_snapshot_path=...)`는 인수본 내부에서 경로 검증을 마친 지도 파일을 바이트 검사 대상으로 받으며, 원래 DATA_ROOT 경로/ID/등록된 이관 별칭/PTS 검사는 그대로 수행한다. 기본 호출과 v0.1 동작은 바뀌지 않는다. 이 형식은 pipeline 공동 계약이나 연결 완료를 뜻하지 않는다.
