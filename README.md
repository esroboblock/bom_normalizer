# BOM Normalizer

BOM Normalizer는 조립용 BOM과 부품 배치 좌표를 읽어 참조번호(Ref)를 기준으로 결합합니다.
결과에는 각 Ref의 원본 정보가 남아 중복, 미결합, 자재 동일성이 불확실한 사례를 검토할 수 있습니다.
라이브러리와 CLI는 로컬에서 실행됩니다.

## 설치 및 실행

Python 3.11 이상이 필요합니다. 저장소를 클론한 뒤 활성화된 Python 환경에 패키지를 설치합니다.

```sh
python -m pip install .
bom-normalizer normalize \
  --bom examples/bom.csv \
  --coordinates examples/coordinates.csv \
  --mapping examples/mapping.json \
  --output normalized.json
```

CLI는 건수와 검토 내역을 표준 오류 스트림(stderr)에 표시하고, 스키마 버전이 포함된 JSON 파일을 생성합니다.
BOM 단독 예제는 조달 방식과 MPN 열이 없는 합성 CSV를 사용합니다.

```sh
python -m bom_normalizer normalize \
  --bom examples/bom-only.csv \
  --mapping examples/mapping-bom-only.json \
  --output normalized-bom-only.json
```

좌표 파일이 없으면 `--coordinates`를 생략할 수 있습니다. 이때 매핑 JSON에는 `bom` 객체만 있어도 됩니다.
좌표 비교를 생략해도 Ref 범위 전개, 수량 대조, 중복 Ref 및 BOM 값 충돌 검증은 실행합니다.
보고서의 `Coordinates not provided (comparison skipped)`는 좌표 비교를 건너뛰었다는 뜻입니다.
헤더만 있는 좌표 파일을 제공하면 비교를 실행하고 좌표가 없는 Ref마다 `bom_only` 검토를 생성합니다.
출력은 UTF-8 JSON입니다. 출력 경로에는 기존 파일이 없어야 합니다. 종료 코드 `0`이면 정규화가 끝나 검토 보고서를 확인할 수 있습니다.
인자나 매핑이 잘못됐거나 파일 입출력이 실패하면 종료 코드 `2`를 반환합니다.
출력 쓰기나 닫기가 실패하면 방금 생성한 결과 파일을 삭제합니다. 삭제까지 실패하면 그 이유도 보고합니다.
v2는 기본적으로 원본 파일 경로를 별칭으로 표시합니다. `--include-paths`로 경로를 포함할 수 있습니다.
`--omit-raw`는 원본 근거를 줄입니다. **정규화된 MPN, 제조사 등 업무값은 익명화하지 않습니다.**
`--schema-version 1`에는 좌표와 전체 원본 근거가 필요하며 원본 경로가 포함됩니다.

합성 예제의 실행 요약은 다음과 같습니다.

```text
Refs 5 (matched 2, ambiguous 0, bom_only 2, coordinate_only 1)
Unresolved tokens 0 BOM, 0 coordinate
Reviews 4
```

### XLSX BOM과 CSV 좌표

다음 코드는 합성 CSV의 BOM을 두 시트로 구성된 XLSX 예제로 만듭니다.
`Parts` 시트의 헤더는 2행입니다. 위 설치 명령을 실행한 환경에서 저장소 루트에서 실행합니다.

```sh
python - <<'PYTHON'
import csv
from pathlib import Path

from openpyxl import Workbook

workbook = Workbook()
workbook.active.title = "Notes"
parts = workbook.create_sheet("Parts")
parts.append(["Synthetic BOM"])
with Path("examples/bom.csv").open(encoding="utf-8", newline="") as source:
    for row in csv.reader(source):
        parts.append(row)
workbook.save("synthetic-bom.xlsx")
PYTHON

bom-normalizer normalize \
  --bom synthetic-bom.xlsx \
  --coordinates examples/coordinates.csv \
  --mapping examples/mapping-xlsx.json \
  --output normalized-mixed.json
```

## Python API

```python
from bom_normalizer import FileMapping, format_report, merge, read_bom, read_coordinates

bom = read_bom(
    "examples/bom.csv",
    FileMapping({"ref": "Designators", "quantity": "Quantity",
                 "sourcing": "Sourcing", "specification": "Specification"}),
)
coordinates = read_coordinates(
    "examples/coordinates.csv",
    FileMapping({"ref": "Name", "x": "East", "y": "North"}, unit="mil"),
)
result = merge(bom, coordinates)
print(format_report(result))
print(next(row for row in result.placements if row.ref == "R1").coordinates[0].x_mm)
```

메모리에 있는 데이터를 처리하려면 헤더와 `RawRow` 레코드를 담은 `Table`을 만들고
`normalize_bom` 또는 `normalize_coordinates`를 호출합니다. 이 함수들과 `merge`는
파일 쓰기나 네트워크 요청을 수행하지 않습니다. `result_dict(result)`를 호출하면
CLI를 거치지 않고 JSON으로 직렬화할 수 있는 데이터를 얻을 수 있습니다.
좌표 입력이 없는 경우에는 `merge(bom)`을 호출합니다.

[공개 입출력 계약](docs/contract.md), [JSON Schema v2](docs/result-v2.schema.json),
[출력 예제](examples/result-v2.json)에서 버전 호환성과 원본 공개 옵션을 확인할 수 있습니다.

### 외부 어댑터에서 값 사전 변환

외부 어댑터는 고객 문서에 정의된 값을 공개 표준값으로 변환해 전달할 수 있습니다.
아래 합성 예제의 `YES-A` 해석은 호출자 규칙입니다. 변환용 열을 따로 추가하면
`RawRow`와 결과의 원본 근거에 변환 전 값도 남습니다.

```python
from bom_normalizer import FileMapping, RawRow, Source, Table, merge, normalize_bom, result_dict

original = {"Position": "R1", "Assembly Flag": "YES-A"}
policy = {"YES-A": "populated"}
adapted = {**original, "Normalized population": policy.get(original["Assembly Flag"], "unknown")}
source = Source("synthetic-input.csv", 2)
table = Table(tuple(adapted), (RawRow(source, adapted),), Source(source.file, 1))
bom = normalize_bom(table, FileMapping({"ref": "Position", "population": "Normalized population"}))
print(result_dict(merge(bom))["placements"][0]["population"])  # populated
```

좌표도 같은 방식으로 `Table`을 만들어 `normalize_coordinates`에 전달할 수 있습니다.
`Source`는 실제 입력의 파일 식별자, 시트와 물리 행을 가리켜야 합니다.
변환 열은 원본과 구분하고, 보존이 필요한 원본 파일과 어댑터 규칙은 호출자가 관리합니다.

## 입력 파일 매핑

[매핑 JSON Schema](docs/mapping.schema.json)는 설정의 정적 구조를 정의합니다.
[예제 매핑](examples/mapping.json)에는 입력 파일별 설정이 하나씩 있습니다.
좌표 파일을 제공할 때는 `coordinates` 객체가 필요합니다.
BOM 단독 실행에 사용할 수 있는 최소 매핑은 다음과 같습니다.

```json
{
  "bom": {
    "columns": {"ref": "Designators"}
  }
}
```

각 `columns` 객체의 키는 정규화 필드명이고, 값은 입력 열 제목입니다.
열 제목은 대소문자와 공백까지 원본과 정확히 일치해야 합니다.
한 원본 열은 매핑 안에서 한 표준 필드에만 연결할 수 있으며, 입력 헤더 자체도 고유해야 합니다.
아래 설정은 같은 원본 열을 두 필드에 연결하므로 CLI에서 종료 코드 `2`와 함께
`bom.columns.value and columns.ref both reference 'Designators'`를 보고합니다.

```json
{"bom": {"columns": {"ref": "Designators", "value": "Designators"}}}
```

CLI는 스키마의 구조 검사에 더해 JSON 중복 키, 원본 열 재사용, 지원 텍스트 인코딩,
실제 헤더·시트 존재 여부, 좌표 파일과 매핑의 조합을 검사합니다.
`header_row`에는 `2`처럼 정수 리터럴을 사용하세요. JSON Schema는 `2.0`도 정수로 판단합니다.

| 입력 | 필수 열 매핑 | 선택 열 매핑 |
| --- | --- | --- |
| BOM | `ref` | `quantity`, `population`, `mount`, `sourcing`, `manufacturer`, `mpn`, `specification`, `value`, `package` |
| 좌표 | `ref`, `x`, `y` | `rotation`, `side` |

CSV와 XLSX 모두 1부터 시작하는 `header_row`로 헤더 행을 지정할 수 있으며 기본값은 1입니다.
CSV는 파일의 실제 줄 번호를 사용하며, 여러 줄에 걸친 레코드는 첫 줄 번호로 식별합니다.
CSV 인코딩은 `encoding`으로 지정하며 기본값은 `utf-8-sig`입니다.
구분자는 `delimiter`로 지정하며 기본값은 쉼표입니다. 줄바꿈과 NUL을 제외한 한 글자를 사용합니다.
예를 들어 CP949와 세미콜론을 사용하는 CSV라면 `"encoding": "cp949", "delimiter": ";"`를 지정합니다.
XLSX에 워크시트가 여러 개 있으면 `sheet`에 정확한 시트 이름을 지정합니다.
시트 미지정은 생략 또는 `null`로 표현하며, CSV에서 시트 이름을 지정하면 오류입니다.
`encoding`과 `delimiter`는 CSV를 읽을 때 적용됩니다.
매핑 대상 셀에 수식이 있다면 처리 전에 계산된 값으로 내보내세요.
수식이 포함된 입력을 읽으면 원본 정보에 수식을 보존하고 검토 항목을 생성합니다.
매핑 대상 XLSX 오류 셀도 원본 정보에 남기고 검토 항목을 생성합니다.

좌표 매핑에는 `unit`을 지정해야 합니다. 지원 단위는 `mm`, `cm`, `um`, `mil`, `in`입니다.
X와 Y에는 소수점으로 마침표를 사용하며, 부호와 과학적 표기법도 사용할 수 있습니다.
X와 Y는 밀리미터로 변환합니다. 회전은 도 단위로 입력하고 각도는 그대로 유지합니다.
좌표 원점, 축 방향, 회전 방향 규약, 기판 면도 입력 기준을 따릅니다.

Ref는 ASCII 영문자 뒤에 숫자가 오는 형식입니다. 영문자는 대문자로 통일하고 숫자 앞의 0은 유지합니다.
BOM에서 여러 Ref는 쉼표, 세미콜론, 공백으로 구분할 수 있습니다.
같은 접두사의 오름차순 범위는 `R1-R4` 또는 `R1-4`로 표기합니다.
앞에 0을 붙인 범위는 양 끝 숫자의 자릿수가 같아야 하며, 한 범위에서 최대 10,000개 Ref를 전개할 수 있습니다.
좌표 행에는 해석 후 Ref가 하나만 있어야 합니다.

| 항목 | 입력값 |
| --- | --- |
| 실장 여부 `population` | 실장은 `populated`, `yes`, `true`, `1`. 미실장은 `dnp`, `dnf`, `no`, `false`, `0`. 미확인은 `unknown`. |
| 실장 방식 `mount` | `smd`, `smt`, `tht`, `through-hole`, `unknown` |
| 조달 방식 `sourcing` | `fixed`, `spec_based`, `unknown` |
| 기판 면 `side` | `top`, `bottom`, `unknown` |
| 규격 `specification` | 명시적인 자재 규격 문자열. 구조화할 수 있는 표기는 [자재 판정 계약](docs/contract.md#자재-판정)을 따릅니다. |

분류값은 대소문자를 구분하지 않습니다. 빈 값은 `unknown`으로 기록하고,
알 수 없는 값은 검토 항목을 생성합니다. 실장 여부와 실장 방식은 BOM에 기재된 값을 기준으로 합니다.
`sourcing` 열이 없거나 값이 비어 있으면 `unknown`을 유지합니다. MPN이 비어 있어도
`spec_based`를 추론하지 않습니다. 열 매핑은 제목을 연결하며, 고객별 용어·패키지 단위계 해석은 외부 어댑터가 담당합니다.

## 결과 해석

`placements`에는 정규화된 Ref당 한 행이 들어가며, Ref 문자열을 기준으로 정렬합니다.
결합 상태는 아래와 같습니다. 결과를 사용하기 전에 관련 검토 항목을 확인하세요.

| 상태 | 의미 |
| --- | --- |
| `matched` | BOM 한 건과 좌표 한 건이 같은 Ref로 결합됐습니다. |
| `ambiguous` | 양쪽에 Ref가 있고 적어도 한쪽에 두 건 이상 있습니다. |
| `bom_only` | Ref가 BOM에만 있습니다. BOM에서 중복된 경우도 포함합니다. |
| `coordinate_only` | Ref가 좌표에만 있습니다. 좌표에서 중복된 경우도 포함합니다. |

JSON에서 좌표 미제공은 `inputs.coordinates.status`의 `not_provided`로 확인합니다.
이 경우에도 BOM 행의 상태는 `bom_only`이며, 좌표 미결합 검토는 생성되지 않습니다.

각 행의 `bom`과 `coordinates` 배열에는 해당 Ref의 입력 정보가 들어갑니다.
각 항목은 `source`(기본 JSON에서는 파일 별칭, 워크시트 이름이 있으면 그 이름, 행 번호),
원본 Ref 표기인 `token`, 열 제목별 원본 셀 값인 `raw`를 포함합니다.
알 수 없는 분류값은 문자열 `unknown`으로, 누락된 선택값은 JSON의 `null`로 기록합니다.
정확한 소수 값을 보존하기 위해 JSON에서는 소수를 문자열로 표현합니다.
예를 들어 R1의 X좌표 100 mil은 `"x_mm": "2.5400"`으로 기록합니다.
해석하지 못한 BOM·좌표 Ref는 각각 별도 배열에 원본 위치 및 데이터와 함께 남습니다.
`issues` 배열에는 검토 코드, 설명, 원본 위치가 들어갑니다.

`material_group`은 한 정규화 결과 안에서만 사용하는 자재 그룹 식별자입니다.
제조사와 MPN은 선택 입력입니다. `fixed` 부품은 두 식별자가 모두 일치하고 BOM 근거가
충돌하지 않을 때 같은 그룹으로 묶습니다. `spec_based` 항목은 구조화된 저항·커패시터
규격이 일치하면 후보로 표시하고, 자재 그룹은 Ref별로 분리합니다.
`material_assessment`에서 확인·후보·보류 상태, 사유, 구조화된 규격을 확인할 수 있습니다.
지원하는 표기와 필수 사양은 [자재 판정 계약](docs/contract.md#자재-판정)에 나와 있습니다.

호출자가 입력 파일 형식과 열 매핑을 지정합니다.
고객별 해석, 양식 자동 감지, 공용 부품 카탈로그 식별, 원가 계산은 패키지 범위 밖입니다.
예제와 테스트는 합성 데이터를 사용합니다.

## 테스트 및 라이선스

```sh
python -m pip install ".[test]"
python -m pytest
```

CI는 Linux Python 3.11–3.14와 Windows Python 3.13에서 패키지를 설치하고 테스트합니다.

패키지는 [MIT 라이선스](LICENSE)를 사용합니다. 런타임 의존성에는 각 프로젝트의 라이선스가 적용됩니다.
[openpyxl](https://openpyxl.readthedocs.io/en/stable/)과 그 의존성인
[et-xmlfile](https://openpyxl.pages.heptapod.net/et_xmlfile/)은 MIT 라이선스를 사용합니다.
[defusedxml](https://github.com/tiran/defusedxml)은 Python Software Foundation 라이선스를 사용하며,
XLSX의 XML을 읽을 때 안전한 파싱을 지원합니다.
