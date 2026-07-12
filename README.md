# 집생활 내비 (ZipLife Navi)

청년·신혼부부·전월세 세입자를 위한 AI 주거·이사 비서 MCP 서버

## 문제 정의

전월세로 이사하는 사람은 짧은 기간 안에 여러 가지를 동시에 처리해야 합니다.

- 내가 받을 수 있는 주거지원/대출/보증 제도가 뭔지 모른다
- 전입신고, 확정일자, 공과금 이전 같은 행정 일정을 놓친다
- 계약·입주·퇴거 시점마다 뭘 확인해야 하는지 매번 검색해야 한다
- 집주인/부동산에 연락할 때 어떻게 말을 꺼내야 할지 막막하다

집생활 내비는 사용자가 자연어로 자신의 상황("나 27살이고 서울에서 월세로 이사해")을 말하면,
① 상황 정리 → ② 혜택 후보 안내 → ③ 이사 타임라인 → ④ 계약 단계별 체크리스트 → ⑤ 연락 문장까지
한 번에 정리해주는 AI 주거생활 비서입니다.

## 타깃 사용자

- 첫 자취/독립을 준비하는 청년 (월세, 전세)
- 신혼부부 (전세자금대출, 신혼희망타운 등 확인 필요)
- 이사를 앞두고 있거나 퇴거를 준비 중인 전월세 세입자

## 대표 사용 시나리오

**시나리오 1. 월세 이사 예정 청년**
> "나 27살이고 서울에서 월세 70만원짜리 집으로 7월 20일에 이사해. 보증금은 1000만원이야. 받을 수 있는 지원 있어?"

`parse_housing_profile` → `match_housing_benefits` → `generate_moving_timeline` → 필요 시 `generate_message_template` 순으로 호출되어, 후보 제도 + 이사 D-day 체크리스트 + 집주인에게 보낼 문장까지 한 번에 안내됩니다.

**시나리오 2. 신혼부부**
> "결혼 예정이고 전세집 알아보는 중이야. 보증금 1억 5천만원 정도 생각하는데 연봉은 4500이야."

`parse_housing_profile`이 혼인 상태·보증금·연봉을 추출하고, `match_housing_benefits`가 신혼부부 전세자금대출, 신혼희망타운 등을 후보로 안내하며 부족한 정보는 `ask_missing_info`로 되묻습니다.

**시나리오 3. 퇴거 예정 세입자**
> "다음 달에 이사 나가는데 보증금 잘 돌려받으려면 뭐 해야 해?"

`check_contract_tasks(stage="보증금반환")`로 체크리스트를 안내하고, `generate_message_template(purpose="보증금반환")`으로 집주인에게 보낼 문장을 바로 만들어줍니다.

## 툴 목록 (9개)

| Tool | 역할 | 주요 입력 | 공통 출력 |
|---|---|---|---|
| `parse_housing_profile` | 자연어에서 나이/지역/구·군/주거형태/보증금/월세/연봉/이사일/혼인여부/계약단계 추출 | `user_text` | summary, detected_profile, missing_fields, next_actions, caution |
| `ask_missing_info` | 부족한 정보를 질문 형태로 생성 | `profile` | summary, questions, next_actions, caution |
| `match_housing_benefits` | 10개 주거지원 제도를 3단계로 매칭. **청년(만 39세 이하)이면 온통청년 실시간 API, 신혼(예정 포함)이면 LH 임대주택단지 실시간 API를 자동 연동** | `profile` | summary, matches[], live_youth_policies?, live_lh_complexes?, next_actions, caution |
| `search_official_youth_policy` | 온통청년(youthcenter.go.kr) 청년정책 오픈API를 실시간 조회 | `keyword`, `region`, `mid_category` | summary, source, policies[], caution |
| `check_market_rent` | 국토교통부 전월세 실거래가 API로 자치구 평균 시세 조회(아파트/오피스텔/빌라/다가구), 사용자 보증금/월세와 비교 | `district`, `property_type`, `year_month`, `user_deposit`, `user_monthly_rent`, `fetch_all` | summary, source, avg_deposit_won, avg_monthly_rent_won, comparison, caution |
| `search_lh_rental_complexes` | LH 임대주택단지 조회 API로 전국 시/도별 공공임대주택 단지 목록(국민임대/행복주택/신혼희망타운 등) 실시간 조회 | `region`, `supply_type_keyword`, `page` | summary, source, complexes[], caution |
| `generate_moving_timeline` | 이사일 기준 D-30~D+7 체크리스트 생성 | `move_date`(YYYY-MM-DD), `housing_type` | summary, timeline[], next_actions, caution |
| `check_contract_tasks` | 계약전/입주/퇴거/보증금반환 단계별 체크리스트 | `stage`, `housing_type` | summary, tasks[], next_actions, caution |
| `generate_message_template` | 12가지 상황 × 3가지 톤 메시지 생성 | `purpose`, `recipient`, `tone` | summary, message, next_actions, caution |

모든 툴은 `readOnlyHint: true`로 부작용이 없으며, 서버는 stateless로 동작합니다(입력값을 저장하지 않음).

## LH 임대주택단지 조회 (한국토지주택공사) — 검증 완료 ✅

`search_lh_rental_complexes`는 [공공데이터포털](https://www.data.go.kr/data/15059475/openapi.do)의 LH 임대주택단지 조회 API를 실시간 호출해, 전국 시/도별 공공임대주택 단지 목록(단지명, 공급유형, 총세대수, 전용면적, 임대보증금, 월임대료)을 가져옵니다. **공식 활용가이드 문서로 정확한 응답 구조를 확인해 반영했습니다.**

- 요청 URL: `https://apis.data.go.kr/B552555/lhLeaseInfo1/lhLeaseInfo1`
- 인증: `PUBLIC_DATA_API_KEY` (다른 국토부 API들과 같은 키 재사용 가능)
- 응답 구조가 독특합니다: `[{"dsSch":...}, {"resHeader":[{"SS_CODE":...}], "dsList":[...실제 단지들...]}]` — 최상위가 2-요소 리스트이고, 그 안의 `dsList`가 실제 데이터입니다. `SS_CODE`가 `"Y"`가 아니면 에러로 처리합니다.
- ⚠️ **임대보증금/월임대료는 이미 '원' 단위**입니다. 국토부 실거래가 API(만원 단위)와 헷갈리지 않도록 처리했습니다.
- **전국 지원, 정확한 지역코드 반영**: 서울(11), 부산(26), 대구(27), 인천(28), 대전(30), 울산(31), 경기(41), 충북(43), 충남(44), 경북(47), 경남(48), 제주(50), 세종(36110, 5자리), 강원(51, 강원특별자치도), 전북(52, 전북특별자치도), 광주/전남(12, 전남광주통합특별시로 행정구역 통합)
- **공급유형 필터**: `supply_type_keyword`가 국민임대(07)/공공임대(08)/영구임대(09)/행복주택(10)/장기전세(11)/매입임대(13)/전세임대(17) 중 정확히 일치하면 `SPL_TP_CD`로 API에 함께 전달합니다. 다만 **실제 테스트 결과 이 API가 SPL_TP_CD 파라미터를 무시하는 것이 확인**되어, 서버 필터를 신뢰하지 않고 응답을 받은 뒤 항상 이름으로 다시 필터링합니다.
- `total_count`(전체 건수)를 통해 페이지네이션 잘림 여부를 안내
- `first_move_in_ym`(최초입주년월)이 간혹 비정상적인 값으로 나올 수 있는데, 이는 LH 원본 데이터 자체의 이상값으로 확인되었습니다(파싱 문제 아님).

## 시세 비교 (국토교통부 전월세 실거래가 API — 4개 주택유형) — 검증 완료 ✅

`check_market_rent`는 [공공데이터포털](https://www.data.go.kr/data/15126474/openapi.do)의 국토교통부 실거래가 API를 실시간 호출해, 자치구 최근 거래 평균 보증금/월세를 계산하고 사용자 입력값과 비교합니다. **실제 발급받은 키로 4개 API 모두 승인받았으며, 아파트/오피스텔은 호출 테스트까지 완료했습니다.**

| property_type | API | 설명 |
|---|---|---|
| `아파트` | `RTMSDataSvcAptRent` | 실제 호출 테스트 완료 |
| `오피스텔` | `RTMSDataSvcOffiRent` | 실제 호출 테스트 완료 |
| `연립다세대` | `RTMSDataSvcRHRent` | 흔히 말하는 "빌라" |
| `단독다가구` | `RTMSDataSvcSHRent` | 건물 전체가 한 명 소유로 등기된 원룸 건물(개별 호실 임대) |

- 인증: 환경변수 `PUBLIC_DATA_API_KEY` (공공데이터포털에서 발급, 자동승인, 4개 API 모두 같은 키로 사용 가능)
- 응답 포맷은 XML이 기본이라, JSON 파싱을 먼저 시도하고 실패하면 XML을 자동 파싱하도록 구현
- **전세/월세 거래를 자동으로 분리**해서 평균을 계산합니다 (한 데이터셋에 섞여 있어, 합쳐서 평균 내면 왜곡되기 때문 — 실제 테스트에서 발견하고 수정한 부분)
- 유형별로 응답 필드명이 다른 것도 흡수해서 처리: 건물명(`aptNm`/`offiNm`/`mhouseNm`), 면적(`excluUseAr`/`totalFloorAr`) 등. 단독/다가구는 건물명·층 정보 자체가 없음(공식 API 스펙상 그러함)
- **일상 용어를 자동으로 인식**: `property_type`에 "빌라", "다가구", "원룸", "투룸", "자취방" 같은 말을 그대로 넣어도 실제 API 유형으로 자동 매핑됩니다. "원룸"처럼 건물 유형이 특정되지 않는 표현은 오피스텔/연립다세대/단독다가구 세 유형을 모두 조회해 합쳐서 보여주고, 유형별로 몇 건씩 나왔는지(`type_breakdown`)도 함께 제공합니다.
- 고시원/셰어하우스 등 확정일자 신고 대상이 아닌 용어는 조회 불가 안내로 응답, 인식 불가능한 용어는 임의로 추정하지 않고 다시 물어보도록 안내
- **페이지네이션 잘림 감지**: 한 번에 최대 200건만 가져오는데(`numOfRows=200`), 실제 거래량이 그보다 많은 경우(예: 강남/서초처럼 거래량 많은 지역) `truncated_types`에 "실제 전체 건수 대비 몇 건만 반영됐는지"를 표시해 평균이 부분 데이터 기준일 수 있음을 투명하게 알립니다.
- **전체 데이터 수집 옵션(`fetch_all`)**: 기본은 빠른 200건 조회. 잘린 경우 `next_actions`에 "사용자에게 먼저 시간이 더 걸릴 수 있음을 안내하고, 원하면 `fetch_all=true`로 다시 호출하라"는 안내가 담겨 있습니다. `fetch_all=true`로 호출하면 페이지네이션으로 전체 거래를 끝까지 가져옵니다(유형당 최대 2,000건 안전 상한, 무한 호출 방지).
- **현재 서울 25개 자치구만 지원** (법정동코드 매핑 필요, 타 지역은 확장 예정)
- 고시원/셰어하우스 등 확정일자 신고 대상이 아닌 주거 형태는 이 API들로 조회 불가능 (공식 통계 자체가 없음)
- 키가 없거나 미지원 지역/빈 결과면 명확한 안내 메시지로 응답 (에러로 죽지 않음)
- `property_type`은 기본값이 없는 필수 파라미터입니다 — 임의로 "아파트"를 가정하지 않도록, 반드시 사용자의 실제 표현(빌라/원룸 등 포함)을 받아 호출해야 합니다.

```bash
export PUBLIC_DATA_API_KEY="발급받은_인증키"  # Decoding 키 사용 (Encoding 키 아님)
```



## 실시간 정부 API 연동 (온통청년 청년정책API) — 검증 완료 ✅

`search_official_youth_policy`는 한국고용정보원이 운영하는 [온통청년 청년정책 오픈API](https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc)를 실시간으로 호출합니다. **실제 발급받은 키로 호출 테스트를 완료했고, 정상적으로 실시간 정책 데이터를 받아옵니다.**

- 요청 URL: `https://www.youthcenter.go.kr/go/ythip/getPlcy`
- 인증: 환경변수 `YOUTHCENTER_API_KEY`에 발급받은 인증키를 설정 (마이페이지 > OPEN API에서 발급, 무료)
- 응답: `rtnType=json`으로 요청해 JSON으로 받음
- **키가 없거나 호출이 실패하면 자동으로 정적 `BENEFITS` 데이터로 폴백**하여 서비스가 끊기지 않습니다.
- 응답 후처리: 빈 문자열/공백 정리, 신청기간을 `2026-04-01 ~ 2026-04-14` 형식으로 변환 + 현재 접수 상태(`접수 중` / `접수 마감` / `접수 예정`) 자동 계산, 이름+주관기관+신청기간이 동일한 재공고성 중복 항목 자동 제거

```bash
export YOUTHCENTER_API_KEY="발급받은_인증키"
python server.py
```

**실제 응답 예시** (`{"keyword": "주거"}`로 호출):
```json
{
  "summary": "온통청년 API에서 '주거' 관련 정책 N건을 실시간으로 확인했습니다.",
  "source": "youthcenter_live_api",
  "policies": [
    {
      "name": "부산 청년 월세 지원",
      "category": "전월세 및 주거급여 지원",
      "support_content": "월 최대 20만원, 최대 24개월 지원",
      "agency": "부산광역시 청년산학국 청년정책과",
      "apply_start": "2026-03-30", "apply_end": "2026-05-29",
      "apply_status": "접수 마감",
      "apply_url": "https://young.busan.go.kr/index.nm?menuCd=37",
      "age_min": "19", "age_max": "34",
      "income_min_manwon": "0", "income_max_manwon": "0",
      "additional_conditions": "부모님과 별도 거주 무주택청년 / 재산기준: 원가구 4억7천만원 이하..."
    }
  ]
}
```

> `marital_status_code_raw`, `income_condition_code_raw`는 온통청년 자체 내부 코드값이며, 공식 코드정의서(엑셀)를 확보하지 못해 임의로 해석하지 않고 원본 그대로 전달합니다. 실제 소득/연령 조건은 `income_min_manwon`/`income_max_manwon`, `age_min`/`age_max`, `additional_conditions` 필드로 충분히 판단 가능합니다.

> 이 API는 "청년" 카테고리 정책만 다룹니다. 신혼부부 전세자금대출, 전세보증금 반환보증(청년 외), 주거급여 등은 소관 기관(주택도시기금, HUG, 복지로)이 달라 현재는 정적 데이터 + 공식 링크로 안내합니다. 추후 확장 가능한 부분입니다.



## 메시지 템플릿 (13가지 상황 × 3가지 톤)

보증금반환 · 하자확인 · 확정일자 · 이사일정 · 수리요청 · 계약연장문의 · 계약해지통보 ·
월세감액요청 · 관리비문의 · 반려동물문의 · 소음문의 · 대출서류협조요청 · 공과금이전문의

각 상황마다 **정중하게 / 단호하게 / 캐주얼하게** 3가지 톤을 제공하며, 상황별 기본 수신자(집주인/관리사무소/통신사·도시가스 고객센터)가 자동 지정됩니다.
`[날짜]`, `[사유]`처럼 대괄호로 표시된 부분은 실제 내용으로 채워 넣어야 한다는 안내가 `next_actions`에 함께 나옵니다.

`check_contract_tasks`는 계약전/입주/**갱신**/퇴거/보증금반환 5단계를 지원합니다. 갱신 단계는 계약갱신청구권, 5% 증액 상한, 묵시적 갱신 여부, 임대인 변경 시 계약 승계 등 실무적으로 자주 놓치는 항목을 담고 있습니다.

`ask_missing_info`는 `region`은 있는데 `district`가 없으면 "정확히 몇 구(군)야?"를 자동으로 물어봅니다(시세 비교/LH 조회에 구 단위 정보가 필요하기 때문).

`parse_housing_profile`은 서울이 아닌 지역이 감지되면 "시세 비교(check_market_rent)는 현재 서울만 지원한다"는 안내를 caution에 자동으로 붙입니다 — 지역별 지원 범위가 툴마다 달라(시세 비교는 서울만, LH 조회는 전국) 생기는 혼선을 줄이기 위함입니다.

## 정책 데이터 (10개, 후보 안내용)

청년 월세지원 · 청년 전월세 보증금 대출 · 중소기업취업청년 전월세보증금대출 · 버팀목 전세자금대출 ·
신혼부부 전세자금대출 · 신혼희망타운/공공임대 확인 · 서울 청년 이사비/중개보수 지원 · 지자체 주거비 지원 ·
전세보증금 반환보증 · 주거급여

각 제도는 나이/지역/주거형태/혼인여부 등 **명백히 어긋나는 조건만** 걸러내고, 소득·자산 기준처럼 매년 바뀌는 항목은
"추가 확인 필요"로 안내하며 `official_check`(무엇을 확인해야 하는지)와 `official_links`(어디서 확인해야 하는지, 실제 클릭 가능한 공식 도메인)를 함께 제공합니다.

| 제도 | 공식 링크 |
|---|---|
| 청년 월세지원 | [복지로](https://www.bokjiro.go.kr) · [마이홈포털](https://www.myhome.go.kr) |
| 청년 전월세 보증금 대출 / 중소기업취업청년 전월세보증금대출 / 버팀목 전세자금대출 / 신혼부부 전세자금대출 | [주택도시기금](https://nhuf.molit.go.kr) |
| 신혼희망타운/공공임대 확인 | [청약홈](https://www.applyhome.co.kr) · [LH](https://www.lh.or.kr) · [SH](https://www.i-sh.co.kr) |
| 서울 청년 이사비/중개보수 지원 | [청년몽땅정보통](https://youth.seoul.go.kr) |
| 지자체 주거비 지원 / 주거급여 | [복지로](https://www.bokjiro.go.kr) · [마이홈포털](https://www.myhome.go.kr) |
| 전세보증금 반환보증 | [HUG 주택도시보증공사](https://www.khug.or.kr) |

이 링크들은 **개별 정책의 세부 조건 페이지가 아니라, 각 소관 기관의 안정적인 상위 포털**입니다. 개별 공고/조건은 자주 바뀌어도 포털 도메인 자체는 잘 바뀌지 않기 때문에, 시간이 지나도 안내가 깨지지 않도록 이 방식을 택했습니다. (지자체별 세부 공고는 포털 내 검색 또는 관할 시/군/구청 홈페이지에서 확인이 필요합니다.)

## 로컬 실행 방법

```bash
pip install "mcp[cli]"
python server.py
```

기본적으로 Streamable HTTP transport로 실행되며, MCP Inspector로 접속해 각 툴을 테스트할 수 있습니다.

```bash
npx @modelcontextprotocol/inspector
```

## 예시 입력/출력

**입력** (`parse_housing_profile`):
```
나 27살이고 서울 관악구에서 월세 70만원짜리 집으로 다음 달 20일에 이사해. 보증금은 천만원이야. 아직 계약 전이야.
```

**출력 (요약)**:
```json
{
  "summary": "다음과 같이 확인됩니다: 서울 관악구, 27세, 월세 거주 예정, 2026-08-20 이사 예정",
  "detected_profile": {
    "age": 27, "region": "서울", "district": "관악구",
    "housing_type": "월세", "move_date": "2026-08-20",
    "deposit": 10000000, "monthly_rent": 700000,
    "contract_status": "before_signing",
    "missing_fields": ["income", "marital_status", "is_homeless"]
  },
  "next_actions": ["ask_missing_info로 부족한 정보를 질문 형태로 정리해보세요.", "match_housing_benefits로 혜택 후보를 확인해보세요."]
}
```

**`match_housing_benefits` 출력 (일부)**:
```json
{
  "summary": "2개 제도가 후보로 확인되었습니다.",
  "matches": [
    {
      "name": "청년 월세지원",
      "status": "추가 확인 필요",
      "why_matched": "청년이면서 월세로 거주 중이거나 거주 예정이라 매칭되었습니다.",
      "missing_info": ["소득(월 기준 추정치)", "무주택 여부"],
      "official_check": ["거주지 지자체 청년월세지원 공고문", "해당 연도 소득/재산 기준표"],
      "caution": "지자체별로 명칭·지원금액·나이 기준이 다릅니다. 반드시 관할 지자체 공고로 재확인하세요."
    }
  ]
}
```

## 주의사항

- **법률/재정 자문이 아닙니다.** 모든 혜택 매칭 결과는 후보 안내이며, 최종 신청 가능 여부는 반드시 공식 공고와 상담을 통해 확인해야 합니다.
- **개인정보를 저장하지 않습니다.** 서버는 stateless로 동작하며, 입력값은 그 요청 처리 후 유지되지 않습니다.
- **최신 기준을 단정하지 않습니다.** 소득·자산 기준처럼 매년 바뀌는 항목은 "확인 필요"로만 안내하고, 확인 가능한 공식 출처를 함께 제공합니다.
