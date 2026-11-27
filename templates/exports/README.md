# 5파일 내보내기 합성 예제

[synthetic](synthetic/record.json) 폴더에 예제 **한 세트**만 둔다. 실제 영상, 실제 관찰, 실제 사용자 검수 결과가 아니다. 합성 3프레임/비영점 PTS/UNKNOWN·YES/null의 차이를 설명하는 자료이며 평가/처리량 근거로 사용할 수 없다.

| 파일 | 내용 |
| --- | --- |
| [record.json](synthetic/record.json) | 선택한 revision 전체. 분석·라벨·기존 시간 필드·이력·확장 필드 보존 |
| [analysis.json](synthetic/analysis.json) | 같은 record의 analysis 부분과 정확히 일치 |
| [label.json](synthetic/label.json) | 같은 record의 label 부분과 정확히 일치 |
| [events.csv](synthetic/events.csv) | 이벤트당 한 행. video/event ID·종류·상태·프레임/PTS 구간·답변·근거·한계. 복합 필드는 JSON 문자열. UTF-8 BOM. 이벤트 0개이면 헤더만 |
| [export-manifest.json](synthetic/export-manifest.json) | 내보낸 시각/목적·영상ID·원본해시·선택 revision 경로/파일해시·형식 버전·검수/평가 상태·앞 4파일 SHA256/바이트 수 |

manifest의 revision_ref 마지막 파일명이 revision 번호다. 자기 자신은 파일 해시 목록에 넣지 않는다. 예제의 원본 해시는 `examples/build_synthetic.py`의 합성 바이트에서 계산한 값이며 실제 재생 가능한 원본이 아니다. 예제 revision_ref는 논리 경로이며 실제 영상별 revision을 선생성한 것이 아니다. 이 예제는 record의 출력 바이트와 합성 revision 바이트가 같으므로 두 파일해시도 같다.

`export_format.bundle_bytes()`는 전달받은 record를 5개 바이트열로 변환할 뿐 디렉토리나 파일을 만들지 않는다. 실제 내보내기 실행기는 3단계에서 **사용자가 검수 화면에서 선택한 저장 revision**을 다시 읽어 검증한 뒤 호출한다. 자동 내보내기·미저장 편집 출력·CVAT XML/ZIP·영상/프레임 복사는 없다. CSV는 전체 관찰/부재/세션 의미를 대체하지 않는다.

합성 예제만 재생성하는 명령(실제 exports 생성 명령이 아님):

```powershell
$env:PYTHONPATH = 'src'
uv run --locked --offline python examples/build_synthetic.py
```

실제5개 선정을 시작하거나 local/exports를 만드는 동작은 없다.

새 행동 의미 프로필(`label.semantic_profile`) 기록은 이 v0.1 5파일 형식으로 표현할 수 없으므로 `bundle_bytes()`·화면 내보내기가 명시 거부한다. 필드를 누락하거나 옛 events.csv 의미로 출력하지 않는다. 별도 [DEV 인수본](../../docs/dev-handoff.md)은 선택한 검수 record/map 바이트를 보존하며 CSV로 변환하지 않는다. 프로필용 CSV와 pipeline 채택 계약은 후속 작업이다.
