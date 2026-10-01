# ZipLife Navi (집생활 내비)

An AI housing and moving assistant MCP server for young adults, newlyweds, and tenants in South Korea.

## Problem Statement

Moving into a rental home requires people to handle multiple tasks within a short period:

- Finding housing assistance programs, rental deposit loans, and guarantee schemes they may qualify for
- Keeping track of administrative tasks such as address registration, obtaining a fixed-date stamp, and transferring utility accounts
- Checking what needs to be done before signing a lease, moving in, moving out, and recovering a deposit
- Writing messages to landlords and real estate agents

ZipLife Navi helps users organize these tasks by describing their circumstances in natural language, such as:

> “I’m 27 and moving into a monthly rental home in Seoul.”

The assistant guides users through:

1. Understanding their housing situation
2. Identifying potential housing support programs
3. Creating a moving timeline
4. Providing checklists for each stage of the lease
5. Drafting messages to relevant contacts

> This is English documentation for a service focused on South Korean housing programs. Tool inputs and responses shown below retain their original Korean values where applicable.

## Target Users

- Young adults preparing to live independently for the first time
- Newlyweds exploring rental deposit loans and public housing options
- Tenants preparing to move into or out of a rental home

In South Korea, **jeonse** refers to a rental arrangement based on a large refundable deposit, while **wolse** generally involves a smaller deposit and monthly rent.

## Example Use Cases

### Scenario 1: A Young Adult Moving into a Monthly Rental Home

> “I’m 27 and moving into a home in Seoul on July 20. The monthly rent is KRW 700,000, and the deposit is KRW 10 million. Is there any support I can apply for?”

The assistant calls:

`parse_housing_profile` → `match_housing_benefits` → `generate_moving_timeline` → `generate_message_template`, if needed.

The result includes potential support programs, a moving checklist, and a message to send to the landlord.

### Scenario 2: A Couple Preparing to Get Married

> “We’re planning to get married and looking for a jeonse home. Our planned deposit is about KRW 150 million, and my annual income is KRW 45 million.”

`parse_housing_profile` extracts marital status, the planned deposit, and annual income.

`match_housing_benefits` identifies potential programs, such as rental deposit loans for newlyweds and newlywed public housing options. Missing information is requested through `ask_missing_info`.

### Scenario 3: A Tenant Preparing to Move Out

> “I’m moving out next month. What should I do to make sure I get my deposit back?”

`check_contract_tasks(stage="보증금반환")` provides a deposit-return checklist.

`generate_message_template(purpose="보증금반환")` generates a message to send to the landlord.

## Available Tools

| Tool | Purpose | Main Inputs | Output Fields |
|---|---|---|---|
| `parse_housing_profile` | Extracts age, region, district, housing arrangement, deposit, monthly rent, annual income, moving date, marital status, and lease stage from natural language | `user_text` | `summary`, `detected_profile`, `missing_fields`, `next_actions`, `caution` |
| `ask_missing_info` | Generates follow-up questions for missing information | `profile` | `summary`, `questions`, `next_actions`, `caution` |
| `match_housing_benefits` | Matches users against 10 housing support programs using three matching statuses. Automatically queries the live Youth Center API for young adults aged 39 or younger and the live LH rental housing API for newlyweds, including couples planning to marry | `profile` | `summary`, `matches[]`, optional `live_youth_policies`, optional `live_lh_complexes`, `next_actions`, `caution` |
| `search_official_youth_policy` | Retrieves current youth policies from the Youth Center Open API | `keyword`, `region`, `mid_category` | `summary`, `source`, `policies[]`, `caution` |
| `check_market_rent` | Retrieves district-level rental transaction data for apartments, officetels, multi-unit homes, and detached or multi-household homes, then compares average deposits and monthly rents with the user's values | `district`, `property_type`, `year_month`, `user_deposit`, `user_monthly_rent`, `fetch_all` | `summary`, `source`, `avg_deposit_won`, `avg_monthly_rent_won`, `comparison`, `caution` |
| `search_lh_rental_complexes` | Retrieves public rental housing complexes by province or metropolitan city through the LH API | `region`, `supply_type_keyword`, `page` | `summary`, `source`, `complexes[]`, `caution` |
| `generate_moving_timeline` | Generates a checklist covering 30 days before to 7 days after the moving date | `move_date` (`YYYY-MM-DD`), `housing_type` | `summary`, `timeline[]`, `next_actions`, `caution` |
| `check_contract_tasks` | Provides checklists for pre-signing, move-in, renewal, move-out, and deposit return | `stage`, `housing_type` | `summary`, `tasks[]`, `next_actions`, `caution` |
| `generate_message_template` | Generates messages for common housing situations in three tones | `purpose`, `recipient`, `tone` | `summary`, `message`, `next_actions`, `caution` |

All nine tools are marked with `readOnlyHint: true`. They do not perform write operations, and the server operates statelessly without retaining user inputs.

## LH Rental Housing Complex API Integration

`search_lh_rental_complexes` calls the [LH Rental Housing Complex API](https://www.data.go.kr/data/15059475/openapi.do) to retrieve public rental housing complexes across South Korea.

The returned information includes:

- Complex name
- Housing supply type
- Total number of units
- Exclusive floor area
- Rental deposit
- Monthly rent

The response structure was implemented using the official API usage guide.

### Endpoint and Authentication

- Endpoint: `https://apis.data.go.kr/B552555/lhLeaseInfo1/lhLeaseInfo1`
- Environment variable: `PUBLIC_DATA_API_KEY`
- The same API key can be reused for the Ministry of Land, Infrastructure and Transport APIs, subject to access approval.

### Response Handling

The API returns an unusual top-level structure:

```json
[
  {"dsSch": "..."},
  {
    "resHeader": [{"SS_CODE": "..."}],
    "dsList": ["..."]
  }
]
```

The actual housing records are contained in `dsList` within the second element.

Responses are treated as errors when `SS_CODE` is not `"Y"`.

### Implementation Details

- **Currency units:** Rental deposits and monthly rents are already expressed in KRW. They are handled separately from the rental transaction APIs, which use units of KRW 10,000.
- **Regional lookup:** Region names are mapped to the codes used by the LH API. The implementation includes special cases such as five-digit codes.
- **Supply-type filtering:** Recognized supply types are sent using `SPL_TP_CD`:

  | Supply Type | Code |
  |---|---|
  | National rental housing (`국민임대`) | `07` |
  | Public rental housing (`공공임대`) | `08` |
  | Permanent rental housing (`영구임대`) | `09` |
  | Happy Housing (`행복주택`) | `10` |
  | Long-term jeonse housing (`장기전세`) | `11` |
  | Purchased rental housing (`매입임대`) | `13` |
  | Jeonse rental housing (`전세임대`) | `17` |

- **Response-side filtering:** During testing, the API was observed to ignore `SPL_TP_CD`. Results are therefore filtered again by supply-type name after retrieval.
- **Pagination visibility:** `total_count` helps indicate whether additional records remain beyond the retrieved page.
- **Source-data anomalies:** `first_move_in_ym` occasionally contains unusual values. These were identified as anomalies in the original LH data rather than parsing errors.

## Rental Market Comparison

`check_market_rent` calls the Ministry of Land, Infrastructure and Transport rental transaction APIs to calculate average deposits and monthly rents for a selected district and compare them with the user's values.

Access was approved for all four APIs. Live request tests were completed for the apartment and officetel APIs.

| `property_type` | API | Description |
|---|---|---|
| `아파트` | `RTMSDataSvcAptRent` | Apartments; live request testing completed |
| `오피스텔` | `RTMSDataSvcOffiRent` | Officetels; live request testing completed |
| `연립다세대` | `RTMSDataSvcRHRent` | Low-rise multi-unit housing, commonly called “villas” in Korea |
| `단독다가구` | `RTMSDataSvcSHRent` | Detached and multi-household homes, including buildings with individually rented rooms |

API reference: [Public Data Portal](https://www.data.go.kr/data/15126474/openapi.do)

### Authentication

Set `PUBLIC_DATA_API_KEY` to the key issued through the Public Data Portal. The same key can be used for all four APIs after obtaining the necessary access approvals.

```bash
export PUBLIC_DATA_API_KEY="YOUR_DECODED_API_KEY"
```

Use the **decoded key**, not the URL-encoded key.

### Implementation Details

- **JSON/XML handling:** The server attempts JSON parsing first and automatically falls back to XML parsing.
- **Separate jeonse and monthly-rent calculations:** Transactions are separated before calculating averages because combining them would distort the results. This issue was identified and corrected during testing.
- **Field normalization:** Differences between API response fields are handled, including building names (`aptNm`, `offiNm`, `mhouseNm`) and floor area (`excluUseAr`, `totalFloorAr`). The detached/multi-household API does not provide building names or floor information.
- **Everyday housing terms:** Korean expressions such as `빌라`, `다가구`, `원룸`, `투룸`, and `자취방` are mapped to the relevant API categories.
- **Ambiguous property types:** Terms such as `원룸` do not uniquely identify a building category. The server queries officetel, multi-unit, and detached/multi-household datasets and combines the results. `type_breakdown` shows the number of records retrieved from each category.
- **Unsupported or unknown terms:** Unsupported housing categories return an explanatory message. Unrecognized terms trigger a request for clarification rather than an arbitrary assumption.
- **Truncation detection:** The default request retrieves up to 200 records per property type (`numOfRows=200`). If more transactions exist, `truncated_types` indicates how many were included relative to the total, making it clear that averages may be based on partial data.
- **Optional extended retrieval:** When results are truncated, `next_actions` tells the assistant to explain the additional processing time and offer another request with `fetch_all=true`.
- **Retrieval limit:** `fetch_all=true` retrieves additional pages, subject to a safety limit of 2,000 records per property type to prevent unbounded requests.
- **Current geographic coverage:** Rental market comparison currently supports Seoul's 25 districts. Other regions require additional district-code mappings.
- **Unavailable categories:** Gosiwon and shared housing cannot currently be queried through these integrations.
- **Graceful failure handling:** Missing API keys, unsupported regions, and empty results produce clear guidance instead of terminating the service.
- **Required property type:** `property_type` has no default value. The assistant must use the user's actual housing description rather than assume an apartment.

## Youth Center Policy API Integration

`search_official_youth_policy` calls the [Youth Center Open API](https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc), operated by the Korea Employment Information Service.

Live requests were tested using an issued API key and successfully returned current policy data.

### Endpoint and Authentication

- Endpoint: `https://www.youthcenter.go.kr/go/ythip/getPlcy`
- Environment variable: `YOUTHCENTER_API_KEY`
- API keys can be obtained free of charge through the site's My Page → OPEN API section.
- Requests use `rtnType=json` to retrieve JSON responses.

```bash
export YOUTHCENTER_API_KEY="YOUR_API_KEY"
python server.py
```

### Fallback and Post-processing

- If the API key is missing or a request fails, the service falls back to the static `BENEFITS` dataset.
- Empty strings and unnecessary whitespace are cleaned.
- Application periods are normalized to a format such as `2026-04-01 ~ 2026-04-14`.
- Application status is calculated as open, closed, or upcoming.
- Duplicate listings with the same name, administering agency, and application period are removed.

### Example Response

The following example retains the original Korean response text. It illustrates the response structure and is not a statement of current program availability.

Request:

```json
{"keyword": "주거"}
```

Response:

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
      "apply_start": "2026-03-30",
      "apply_end": "2026-05-29",
      "apply_status": "접수 마감",
      "apply_url": "https://young.busan.go.kr/index.nm?menuCd=37",
      "age_min": "19",
      "age_max": "34",
      "income_min_manwon": "0",
      "income_max_manwon": "0",
      "additional_conditions": "부모님과 별도 거주 무주택청년 / 재산기준: 원가구 4억7천만원 이하..."
    }
  ]
}
```

`marital_status_code_raw` and `income_condition_code_raw` contain internal Youth Center codes. Because an official code-definition spreadsheet was not available, these values are returned unchanged rather than interpreted speculatively.

The fields `income_min_manwon`, `income_max_manwon`, `age_min`, `age_max`, and `additional_conditions` provide information for reviewing the applicable conditions.

### Coverage Limitations

This API covers policies categorized as youth policies.

Programs administered by other organizations—such as newlywed rental deposit loans, rental deposit return guarantees for non-youth applicants, and housing benefits—are currently provided through static program information and official links.

Additional agency integrations can be added in the future.

## Message Templates

The service provides templates for 13 common situations, each available in three tones:

- Deposit return
- Property defect confirmation
- Fixed-date stamp arrangements
- Moving schedule coordination
- Repair requests
- Lease renewal inquiries
- Lease termination notices
- Rent reduction requests
- Maintenance fee inquiries
- Pet-related inquiries
- Noise complaints
- Requests for cooperation with loan documentation
- Utility account transfers

Available tones:

- Polite
- Firm
- Casual

Each situation has a default recipient, such as a landlord, property management office, or telecommunications or gas utility customer service team.

Placeholders such as `[날짜]` (“date”) and `[사유]` (“reason”) must be replaced with the user's actual details. This instruction is included in `next_actions`.

### Lease-stage Checklists

`check_contract_tasks` supports five stages:

1. Before signing
2. Move-in
3. Renewal
4. Move-out
5. Deposit return

The renewal checklist includes items commonly overlooked in practice, such as renewal rights, the applicable rent-increase cap, implied renewal, and lease succession following a change of landlord. Users are directed to verify the rules applicable to their circumstances.

### Missing Information and Coverage Guidance

- If `region` is present but `district` is missing, `ask_missing_info` asks for the specific district or county.
- When a region outside Seoul is detected, `parse_housing_profile` adds a notice to `caution` explaining that `check_market_rent` currently supports Seoul only.
- This helps distinguish tool-specific coverage: rental market comparison is limited to Seoul, while LH rental housing searches support nationwide lookup.

## Housing Support Dataset

The static dataset contains 10 housing support categories for identifying potential options:

1. Monthly rent support for young adults
2. Rental deposit loans for young adults
3. Rental deposit loans for young employees of small and medium-sized enterprises
4. Beotimmok jeonse loans
5. Jeonse loans for newlyweds
6. Newlywed Hope Town and public rental housing options
7. Seoul moving-cost and brokerage-fee support for young adults
8. Local government housing-cost support
9. Rental deposit return guarantees
10. Housing benefits

Matching excludes only clearly incompatible conditions, such as age, region, housing arrangement, or marital status.

Conditions that may change annually, including income and asset thresholds, are marked as requiring further verification.

Each result includes:

- `official_check`: What the user should verify
- `official_links`: Where the user can verify it

### Official Sources

| Program Category | Official Sources |
|---|---|
| Monthly rent support for young adults | [Bokjiro](https://www.bokjiro.go.kr) · [MyHome](https://www.myhome.go.kr) |
| Youth rental deposit loans, SME employee rental deposit loans, Beotimmok loans, and newlywed jeonse loans | [National Housing and Urban Fund](https://nhuf.molit.go.kr) |
| Newlywed Hope Town and public rental housing | [ApplyHome](https://www.applyhome.co.kr) · [LH](https://www.lh.or.kr) · [SH](https://www.i-sh.co.kr) |
| Seoul moving-cost and brokerage-fee support for young adults | [Seoul Youth Portal](https://youth.seoul.go.kr) |
| Local government housing-cost support and housing benefits | [Bokjiro](https://www.bokjiro.go.kr) · [MyHome](https://www.myhome.go.kr) |
| Rental deposit return guarantees | [Korea Housing & Urban Guarantee Corporation (HUG)](https://www.khug.or.kr) |

These links point to the responsible organizations' main portals rather than individual program announcements.

Individual announcements and eligibility pages change frequently. Linking to the main portals helps keep the guidance usable over time.

Users should search within the relevant portal or visit their local government's website to confirm detailed announcements and conditions.

## Local Setup

Install the MCP package and start the server:

```bash
pip install "mcp[cli]"
python server.py
```

The server uses Streamable HTTP transport by default.

Use MCP Inspector to connect to the server and test its tools:

```bash
npx @modelcontextprotocol/inspector
```

Configure API keys to enable live government data integrations:

```bash
export PUBLIC_DATA_API_KEY="YOUR_DECODED_API_KEY"
export YOUTHCENTER_API_KEY="YOUR_YOUTHCENTER_API_KEY"

python server.py
```

## Example Input and Output

### Housing Profile Extraction

Input to `parse_housing_profile`:

```text
나 27살이고 서울 관악구에서 월세 70만원짜리 집으로 다음 달 20일에 이사해. 보증금은 천만원이야. 아직 계약 전이야.
```

English translation:

> “I’m 27 and moving into a monthly rental home in Gwanak-gu, Seoul, on the 20th of next month. The monthly rent is KRW 700,000, and the deposit is KRW 10 million. I haven’t signed the lease yet.”

Example output:

The date below is illustrative; the interpretation of “next month” depends on the request date.

```json
{
  "summary": "다음과 같이 확인됩니다: 서울 관악구, 27세, 월세 거주 예정, 2026-08-20 이사 예정",
  "detected_profile": {
    "age": 27,
    "region": "서울",
    "district": "관악구",
    "housing_type": "월세",
    "move_date": "2026-08-20",
    "deposit": 10000000,
    "monthly_rent": 700000,
    "contract_status": "before_signing",
    "missing_fields": [
      "income",
      "marital_status",
      "is_homeless"
    ]
  },
  "next_actions": [
    "ask_missing_info로 부족한 정보를 질문 형태로 정리해보세요.",
    "match_housing_benefits로 혜택 후보를 확인해보세요."
  ]
}
```

### Housing Benefit Matching

Partial output from `match_housing_benefits`:

```json
{
  "summary": "2개 제도가 후보로 확인되었습니다.",
  "matches": [
    {
      "name": "청년 월세지원",
      "status": "추가 확인 필요",
      "why_matched": "청년이면서 월세로 거주 중이거나 거주 예정이라 매칭되었습니다.",
      "missing_info": [
        "소득(월 기준 추정치)",
        "무주택 여부"
      ],
      "official_check": [
        "거주지 지자체 청년월세지원 공고문",
        "해당 연도 소득/재산 기준표"
      ],
      "caution": "지자체별로 명칭·지원금액·나이 기준이 다릅니다. 반드시 관할 지자체 공고로 재확인하세요."
    }
  ]
}
```

This result identifies a potential program and explains:

- Why it was matched
- Which information is missing
- What the user needs to verify
- Which limitations apply

## Important Notes

- **This service does not provide legal or financial advice.** Benefit matches identify potential options. Final eligibility must be confirmed through official announcements and the responsible organizations.
- **The server does not retain personal information.** It operates statelessly, and user inputs are not retained after processing the request.
- **Changing criteria are not presented as definitive.** Income and asset thresholds that may change annually are marked as requiring verification, with official sources provided.
