# 정규화 입출력 계약

## Python 입력

| 모델과 함수 | 계약 |
| --- | --- |
| `FileMapping`, `read_table(path, mapping)` | CSV/XLSX에서 열 제목, 시트, 헤더 행을 지정합니다. 좌표 매핑은 원본 단위도 지정합니다. |
| `Table(headers, rows, header_source=None)` | 메모리 입력은 열 제목과 순서가 있는 `RawRow`의 튜플을 사용합니다. 각 행의 `raw`에는 열 제목을 키로 원래 셀 값을 넣습니다. `Source(file, row, sheet=None)`의 `file`에는 호출자 소유의 파일 ID도 사용할 수 있습니다. |
| `BomInput`, `CoordinateInput` | `read_bom`/`read_coordinates` 또는 `normalize_bom`/`normalize_coordinates`의 결과입니다. 각각 `entries`, `unresolved`, `issues`, 선택적인 `evidence`를 담습니다. `InputEvidence`는 매핑과 헤더 위치를 기록합니다. |
| `merge(bom, coordinates=None)` | 한 PCB에 해당하는 입력을 Ref로 결합해 `Result`를 반환합니다. 두 번째 인자를 생략하거나 `None`으로 전달하면 좌표 입력 미제공입니다. 빈 `CoordinateInput`은 좌표를 제공한 경우입니다. |

`Source.row`는 1부터 시작합니다. CSV에서 여러 줄에 걸친 레코드는 시작 줄을,
XLSX는 시트 내 행 번호를 사용합니다. 헤더 아래 데이터가 있는 행의 이름 있는 열은
매핑 여부와 관계없이 보존합니다. CSV 셀은 문자열이고 필드 자체가 없는 경우는
`None`입니다. XLSX 셀 값은 판독기가 해석한 값입니다. 수식과 오류는 별도 자료형으로
보존합니다. 서식, 수식 계산 캐시, 빈 행, 헤더 앞의 내용과 파일 바이트를 보관하려면
호출자가 원본 파일을 별도로 보관합니다.

## JSON 결과

[JSON Schema v2](result-v2.schema.json)와 [CLI 출력 예제](../examples/result-v2.json)를
참조하세요. Python `result_dict(result)`와 CLI의 기본 출력은 같은 계약을 사용합니다.
파이썬 dataclass는 메모리용 타입이며 JSON 소비자는 먼저 `schema_version`을 확인합니다.
JSON은 일반 JSON 파서로 읽고 스키마로 검증합니다. 날짜, 수식 등 원본 셀의 형식은 아래
태그로 복원할 수 있습니다.

- `inputs`는 BOM과 좌표 제공 여부, 사용한 매핑, 헤더의 출처를 담습니다. 직접 만든 Python
  입력처럼 매핑이나 헤더 위치를 알 수 없으면 해당 속성은 `null`입니다.
- `placements`의 `Placement`는 Ref당 한 행입니다. `bom`과 `coordinates`에는
  동일 Ref에 대한 모든 원본 항목이 순서대로 남습니다. `token`은 정규화 이전 Ref 토큰이며
  범위를 전개한 경우 여러 항목에서 같은 토큰을 참조합니다. 각 항목의 `raw`는 행 전체를 담습니다.
- `unresolved_bom`과 `unresolved_coordinates`는 해석하지 못한 토큰과 원본 행을
  보존합니다. 같은 행에서 유효 토큰과 무효 토큰이 발견되면 결합 결과와 미해석 배열 양쪽에
  근거가 남습니다. Ref가 없는 행은 `token: null`로 기록됩니다.
- `issues`의 `Review`에는 안정적인 `code`, `severity`, 관련 `sources`, 해당되는
  경우 `ref`와 매핑 필드명인 `field`가 있습니다. `input_role`은 입력별 검토를 BOM 또는
  좌표에 연결하고 결합 단계의 검토에서는 `null`입니다. 위치와 매핑으로 원본 셀을
  찾을 수 있습니다. `message`는 사람에게 표시하는 설명이며 문자열 형식은 달라질 수 있습니다.

| 표현 | 의미 |
| --- | --- |
| `unknown` | 실장 여부, 실장 방식, 조달 방식, 기판 면 같은 분류값을 확정하지 못했습니다. 빈 분류 셀, 인식 불가 값 또는 상충하는 BOM 근거가 원인일 수 있습니다. |
| `null` | 해당 필드에 사용할 값이 없습니다. 예를 들어 미지정 MPN은 `null`입니다. 숫자 또는 원본 셀의 해석이 실패한 경우에는 검토 코드와 원문을 함께 확인합니다. |
| `not_provided` | `inputs.coordinates.status`에서 좌표 입력 자체가 생략됐음을 나타냅니다. 좌표 배열은 비어 있고 BOM 행의 결합 상태는 `bom_only`지만 미결합 검토는 생성되지 않습니다. |
| `provided` + `bom_only` | 좌표 입력을 읽었지만 해당 Ref의 좌표 항목이 없습니다. 헤더만 있는 파일도 여기에 해당하며 `bom_only` 검토가 생성됩니다. |

`matched`는 Ref로 BOM과 좌표가 각각 한 건 결합됐다는 뜻입니다. 원본 값의 오류나
분류 미확정은 `issues`와 항목 값에서 독립적으로 확인합니다. 양쪽에 Ref가 있고
어느 한쪽이 중복이면 `ambiguous`입니다. 한쪽만 있으면 `bom_only` 또는
`coordinate_only`이며, 중복 여부는 배열 길이와 검토 코드에서 확인합니다.

실장 여부와 방식은 BOM 항목이 동의하면 그 값을 사용하고, 상충하거나 BOM 근거가
없으면 `unknown`입니다.

### 자재 판정

제조사와 MPN은 선택 입력입니다. 입력한 `sourcing` 값으로 지정 부품(`fixed`),
규격 기반(`spec_based`), 판정 불가(`unknown`)를 구분합니다. v2 결과의
`placements[].material_assessment`에는 `status`, 판정 코드인 `reason`,
구조화된 `specification`, 선택적인 `candidate_group`이 있습니다.

| 상태 | 근거와 결과 |
| --- | --- |
| `confirmed` | `fixed`의 제조사와 MPN이 모두 있고, 같은 제조사·MPN을 기재한 `fixed` 항목에 상충하거나 읽을 수 없는 자재 근거가 없습니다. 동일한 키는 하나의 `material_group`을 사용합니다. 이 확인의 범위는 입력 BOM에 기재된 식별자입니다. |
| `candidate` | 서로 다른 Ref의 `spec_based` 항목에서 완전한 구조화 규격, 실장 방식, 선택적으로 입력한 제조사·MPN이 모두 일치합니다. `candidate_group`을 공유하며 `material_group`은 Ref별로 부여합니다. 대체 가능성은 별도로 확인해야 합니다. |
| `held` | 규격이 부족하거나, 값이 충돌하거나, 일치 후보가 없는 경우 등입니다. BOM 항목이 한 건이면 Ref별 그룹을 부여하고, BOM 항목이 없거나 중복이면 그룹을 비워 둡니다. |

근거는 각 배치의 `bom` 배열에서 원본 열과 행으로 추적할 수 있습니다. 식별자가
같은 지정 부품 간에 충돌이 있으면 검토 항목에 관련 BOM 행도 남습니다.
저항·커패시터로 명시한 `fixed` 항목은 해석할 수 없는 규격이나 조건을 기재했거나
패키지 단위계가 모호하면 보류합니다. 규격 일부를 생략했거나 커넥터처럼 구조화
대상 밖인 부품은 제조사와 MPN을 기준으로 판정할 수 있습니다.
후보와 자재 그룹 ID는 현재 결과 안에서만 유효합니다. 기존 v2 결과에는
`material_assessment`가 생략될 수 있습니다. v1 출력은 기존 키 구조를 유지하며
`material_assessment`를 생략하지만 그룹에는 같은 보수적 판정 규칙을 적용합니다.

#### 구조화 규격 입력

`specification`의 첫 토큰에 `resistor` 또는 `capacitor`를 적고, 아래 항목을
공백·쉼표·세미콜론으로 구분합니다. 저항값과 정전용량은 `value` 열에도 넣을 수
있습니다. 두 열에서 같은 항목을 선언하면 정규화 값이 일치해야 합니다.

| 범주 | 필요한 항목 | 예시 |
| --- | --- | --- |
| 저항 | 저항값, 허용오차, 정격 전력, 기술 유형(`thick-film`, `thin-film`) | `resistor 10kΩ ±1% 100mW thick-film` |
| 커패시터 | 정전용량, 허용오차, 정격 전압, 유전체(`C0G`, `X5R`, `X7R`) | `capacitor 100nF 10% 50V X7R` |

`package` 열에는 네 자리 코드와 단위계를 함께 적습니다. 예를 들어
`0603 imperial`과 `0603 metric`은 서로 다른 값입니다. 코드만 있으면
`ambiguous_package`로 보류합니다. 단위계 간 코드 변환은 하지 않습니다.
칩 패키지를 명시한 저항·커패시터의 실장 방식이 `tht`이면 충돌로 처리합니다.

저항값은 `Ω` 또는 `ohm`과 `m`, `k`/`K`, `M` 접두사를 지원합니다.
범주가 저항으로 명시된 경우 `10K` 같은 축약형도 지원합니다.
정전용량은 F, mF, µF(또는 uF), nF, pF를 지원합니다. 전력은 W/mW,
전압은 V/mV, 허용오차는 %를 사용합니다. 저항에 전압도 명시하면 비교에 포함합니다.
구조화 결과는 Ω, F, %, W, V 단위의 정확한 십진 문자열을 사용합니다.
알 수 없는 사양이나 조건이 있으면 보류하고, 인식한 값만 구조화합니다. 누락된
정격, 패키지 단위계, 환경·사용 조건에 기본값을 채우지 않습니다.

보류 코드는 `unknown_sourcing`, `missing_fixed_identifier`,
`conflicting_fixed_evidence`, `invalid_material_evidence`,
`incomplete_specification`, `ambiguous_package`, `unsupported_specification`,
`conflicting_specification`, `no_matching_specification`, `duplicate_bom`,
`no_bom`입니다. 단일 BOM 항목의 미확정·충돌 근거는
`ambiguous_material_identity` 검토에 표시합니다. 완전한 규격의 상대가 없다는
이유만으로 검토를 추가하지 않습니다.

| 심각도 | 기본 코드 |
| --- | --- |
| `error` | `missing_ref`, `invalid_ref`, `multiple_coordinate_refs`, `invalid_quantity`, `invalid_coordinate`, `invalid_rotation`, `unsupported_formula`, `source_cell_error` |
| `warning` | `quantity_mismatch`, `unknown_population`, `unknown_mount`, `unknown_sourcing`, `unknown_side`, `bom_only`, `coordinate_only`, `duplicate_bom_ref`, `duplicate_coordinate_ref`, `bom_conflict`, `coordinate_conflict`, `ambiguous_material_identity` |

`error`는 해당 근거를 해석할 수 없었다는 의미이며, 결과가 생성되면 CLI 종료 코드는
`0`입니다. 파일 읽기나 매핑 설정 자체가 실패하면 CLI 종료 코드는 `2`입니다.
검토 항목은 재검토에 사용할 사실과 판단 근거를 담습니다. 해결 상태와 담당자는
어댑터에서 관리합니다.

### 값 인코딩과 재현성

정규화 좌표와 회전은 정확한 십진 문자열입니다. 원본 셀의 문자열, 불리언, 정수,
유한 실수, 빈 값은 JSON 기본 자료형을 사용합니다. 나머지 지원 자료형은 아래처럼
구분합니다.

| `type` | 속성 |
| --- | --- |
| `decimal` | `value`에 Decimal 문자열 |
| `date`, `datetime`, `time` | `value`에 ISO 형식 문자열 |
| `timedelta` | `value`에 부호 있는 총 마이크로초의 정수 문자열 |
| `float` | `value`에 `nan`, `inf`, `-inf` 중 하나 |
| `formula` | `text`에 수식 또는 `null`, `attributes`에 수식 속성 |
| `cell_error` | `error`에 XLSX 오류 문자열 |

메모리 입력의 `raw`도 위 셀 자료형을 사용합니다. v2 전체 근거 출력은 셀에
중첩 컨테이너나 사용자 정의 객체가 들어 있으면 오류를 반환합니다. 기존 메모리
입력의 JSON 호환 컨테이너를 그대로 출력해야 하면 v1을 선택할 수 있습니다.

동일한 입력 내용과 행 순서, 매핑, 출력 옵션을 사용하면 같은 결과가 생성됩니다.
Ref는 문자열 순서로 정렬하고 증거 배열은 입력 순서를 유지합니다. 검토는 BOM,
좌표, Ref별 결합 순으로 생성합니다. 자재 그룹 ID와 파일 별칭은 한 결과 안에서만
유효합니다. 원본 정수의 정밀도가 필요한 소비자는 큰 JSON 정수를 정확히 읽는
파서를 사용합니다.

### 공개 범위와 버전

기본 v2 출력(`evidence_mode: full`)은 `source.file`에 `source-1` 같은 결과 내
별칭을 사용합니다. 파일 경로까지 필요한 내부 작업은 `include_paths=True` 또는
`--include-paths`를 사용합니다. 기본 v2에는 시트 이름, 원문 셀, 토큰, 매핑과 검토
설명이 포함됩니다. 원문 제공을 줄이려면 `include_raw=False` 또는 `--omit-raw`를
사용합니다. 이 경우 `evidence_mode`는 `omit_raw`이며 원문 셀, 토큰, 매핑, 검토
설명은 `null`이고 시트는 별칭으로 표시됩니다. 정규화된 MPN, 제조사, 규격과 같은
업무값은 이 옵션에서도 유지되므로 외부에 공유할 필드는 호출자가 선택합니다.
입력 파일을 열 수 없는 경우 등의 CLI 진단에는 로컬 경로가 포함될 수 있습니다.

v0.1.0의 JSON은 `schema_version: 1`입니다. 기존 v1 소비자는
`result_dict(result, schema_version=1)` 또는 CLI의 `--schema-version 1`로
기존 키 구조와 원본 경로를 받을 수 있습니다. v1을 선택하려면 좌표 입력과 전체
원본 근거가 필요합니다. v2에서는 정규화 좌표의 십진 문자열과 Ref 배열 구조를
유지하며, 입력 제공 상태와 검토 필드를 추가하고 원본 특수 셀을 태그로 구분합니다.
호환 가능한 필드 및 검토 코드는 같은 버전에 추가될 수 있습니다. 기존 필드의 삭제,
타입 변경, 상태 의미 변경에는 새 스키마 버전을 부여합니다. 패키지 버전은 별도로 관리합니다.

## ESSMT 어댑터

어댑터는 v2를 검증하고 `schema_version`을 결과와 함께 기록합니다. 제품 및 리비전 문맥과
원본 파일 스냅샷은 어댑터에서 연결합니다. 경로 대신 안정적인 원본 식별자를 유지하려면
`Table`과 `Source`에 어댑터 내부 ID를 사용하고 `include_paths=True`를 지정할 수
있습니다. 각 Ref의 배열과 미해석 항목을 보관하고, 파일·시트·행의 조합으로 검토
근거를 연결합니다. `material_group`은 해당 결과에서만 유효하므로 자재 마스터 ID와
별도로 다룹니다. 데이터베이스 저장, 파일 스냅샷과 업무 승인 상태는 어댑터의 책임입니다.
