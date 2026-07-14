import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP


mcp = FastMCP(
    "ZipLife Navi",
    json_response=True,
    host="0.0.0.0",  
    port=int(os.environ.get("PORT", 8000)),
)


# ---------------------------------------------------------------------------
# 공통 유틸: 한글 숫자 / 금액 파싱
# ---------------------------------------------------------------------------

KOR_NUM = {"영": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
KOR_UNIT_SMALL = {"십": 10, "백": 100, "천": 1000}
KOR_CHARS = set(KOR_NUM) | set(KOR_UNIT_SMALL)
# 금액 청크를 캡처할 때는 억/만 단위 문자까지 포함해야 "1억 5천만원" 같은 복합 표현이 끊기지 않는다.
KOR_AMOUNT_CHARS = KOR_CHARS | {"억", "만"}


def _kor_to_int(s: str) -> int:
    """순수 한글 숫자 문자열(만/억 제외)을 정수로 변환. 예: '칠십'->70, '삼백오'->305"""
    total = 0
    current = 0
    for ch in s:
        if ch in KOR_NUM:
            current = KOR_NUM[ch]
        elif ch in KOR_UNIT_SMALL:
            current = (current or 1) * KOR_UNIT_SMALL[ch]
            total += current
            current = 0
    total += current
    return total


def _mixed_to_int(s: str) -> int | None:
    """숫자/한글 숫자가 섞인 문자열(만/억 단위 제외)을 정수로 변환. 예: '5천'->5000, '80'->80, '삼백'->300"""
    s = s.strip()
    if not s:
        return None
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = re.fullmatch(r"(\d+)(십|백|천)", s)
    if m:
        return int(m.group(1)) * KOR_UNIT_SMALL[m.group(2)]
    if all(ch in KOR_CHARS for ch in s):
        return _kor_to_int(s)
    return None


UNIT_ONLY_CHARS = {"억", "만", "천", "백", "십"}


def _scan_amount_chunk(text: str, start: int) -> str:
    """
    start 위치부터 금액 문자열(숫자+한글단위)을 안전하게 스캔한다.
    아라비아 숫자가 한 번이라도 등장한 뒤에는, 뒤따르는 한글 문자를 억/만/천/백/십
    같은 '단위 문자'만 허용하고 일/이/삼 같은 '순한글 숫자 단어'는 허용하지 않는다.
    (예: '연봉은 4500이야'에서 '이'가 숫자 2로 오인되어 '4500이'로 붙는 것을 방지)
    순한글 숫자 표현('월세 칠십' 등)은 아라비아 숫자가 전혀 없을 때만 전체 허용한다.
    """
    n = len(text)
    i = start
    seen_digit = False
    chars: list[str] = []

    while i < n:
        ch = text[i]
        if ch.isdigit():
            seen_digit = True
            chars.append(ch)
            i += 1
            continue
        if ch in KOR_AMOUNT_CHARS:
            if seen_digit and ch not in UNIT_ONLY_CHARS:
                break
            chars.append(ch)
            i += 1
            continue
        if ch == " ":
            j = i
            while j < n and text[j] == " ":
                j += 1
            if j < n and (text[j].isdigit() or (text[j] in KOR_AMOUNT_CHARS and not (seen_digit and text[j] not in UNIT_ONLY_CHARS))):
                i = j
                continue
            break
        break

    return "".join(chars)


def _money_to_won(text: str, label: str) -> int | None:


    # '칠십이에요'에서 문법적인 '이'가 숫자 2로 붙는 문제 방지
    # 칠십이에요 → 칠십에요
    normalized_text = re.sub(
        r"(?<=[십백천만억])이(?=에요)",
        "",
        text,
    )

    label_pattern = rf"{re.escape(label)}\s*(?:은|는|이|가)?\s*"

    # 같은 단어가 문장에 여러 번 등장할 수 있으므로 모두 확인
    for label_match in re.finditer(label_pattern, normalized_text):
        chunk = _scan_amount_chunk(
            normalized_text,
            label_match.end(),
        )

        # '월세이고'처럼 뒤에 금액이 없는 경우 다음 '월세'를 찾는다.
        if not chunk:
            continue

        total = 0
        remainder = chunk

        if "억" in remainder:
            eok_str, remainder = remainder.split("억", 1)
            eok_val = _mixed_to_int(eok_str) if eok_str else 1
            total += (eok_val or 1) * 100_000_000

        if "만" in remainder:
            man_str, remainder = remainder.split("만", 1)
            man_val = _mixed_to_int(man_str) if man_str else 1
            total += (man_val or 1) * 10_000

        elif remainder and total == 0:
            value = _mixed_to_int(remainder)

            # 단위가 생략된 주거비·소득 표현은 만원 단위로 해석
            if value is not None:
                total += value * 10_000

        if total > 0:
            return total

    return None



# ---------------------------------------------------------------------------
# 날짜 파싱
# ---------------------------------------------------------------------------

def _parse_move_date(text: str) -> str | None:
    today = date.today()

    # 1) 절대 날짜: 2026년 8월 3일 / 2026-08-03 / 2026.8.3
    match = re.search(r"(\d{4})[.\-년]\s*(\d{1,2})[.\-월]\s*(\d{1,2})일?", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()

    # 2) 상대 월 표현: 다음 달 20일 / 이번 달 5일 / 다다음 달 3일
    rel_match = re.search(r"(다다음|다음|이번)\s*달\s*(\d{1,2})일", text)
    if rel_match:
        offset = {"이번": 0, "다음": 1, "다다음": 2}[rel_match.group(1)]
        day = int(rel_match.group(2))
        year, month = today.year, today.month + offset
        while month > 12:
            month -= 12
            year += 1
        return date(year, month, day).isoformat()

    # 3) 월/일만 있는 경우: 7월 20일 (연도 생략 -> 이미 지났으면 내년으로 보정)
    match = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text)
    if match:
        m, d = int(match.group(1)), int(match.group(2))
        year = today.year
        try:
            result = date(year, m, d)
        except ValueError:
            return None
        if result < today:
            result = date(year + 1, m, d)
        return result.isoformat()

    return None


# ---------------------------------------------------------------------------
# 프로필 필드 정의
# ---------------------------------------------------------------------------

REQUIRED_FIELD_LABELS = {
    "age": "나이",
    "region": "지역",
    "housing_type": "주거 형태(월세/전세/반전세)",
    "move_date": "이사일",
    "deposit": "보증금",
    "monthly_rent": "월세 금액(매달 내는 돈)",
    "income": "소득(월 기준 추정치)",
    "marital_status": "혼인 여부",
    "is_homeless": "무주택 여부",
    "contract_status": "계약 진행 상태",
}
def _is_missing(value: Any) -> bool:
    """None 또는 빈 문자열만 누락으로 판단한다.

    False와 0은 사용자가 입력한 유효한 값이므로 누락이 아니다.
    """
    if value is None:
        return True

    if isinstance(value, str) and not value.strip():
        return True

    return False


def _profile_missing(profile: dict[str, Any]) -> list[str]:
    required_fields = list(REQUIRED_FIELD_LABELS)

    # 전세 사용자는 월세 금액을 입력할 필요가 없다.
    if profile.get("housing_type") not in ("월세", "반전세"):
        required_fields.remove("monthly_rent")

    return [
        key
        for key in required_fields
        if _is_missing(profile.get(key))
    ]


def _profile_summary_text(profile: dict[str, Any]) -> str:
    parts = []
    if profile.get("region"):
        parts.append(f"{profile['region']}" + (f" {profile['district']}" if profile.get("district") else ""))
    if profile.get("age"):
        parts.append(f"{profile['age']}세")
    if profile.get("marital_status") == "married_or_planning":
        parts.append("신혼(예정 포함)")
    if profile.get("housing_type"):
        parts.append(f"{profile['housing_type']} 거주 예정")
    if profile.get("move_date"):
        parts.append(f"{profile['move_date']} 이사 예정")

    if not parts:
        return "아직 확인된 정보가 많지 않습니다. 몇 가지만 더 알려주시면 후보를 정리해드릴게요."
    return "다음과 같이 확인됩니다: " + ", ".join(parts)


# ---------------------------------------------------------------------------
# 정책 데이터 (10개, 최신 공고 확인 필요 전제)
# ---------------------------------------------------------------------------
# region_scope: "전국" 이면 지역 무관, 리스트면 해당 지역(혹은 해당 지역 포함)에서만 운영 가능성 높음
# conditions는 "명백히 어긋나는 경우"만 걸러내기 위한 최소한의 규칙이며,
# 실제 소득/자산 기준 등은 공식 공고 확인이 반드시 필요하다는 전제를 깐다.

BENEFITS: list[dict[str, Any]] = [
    {
        "id": "youth_monthly_rent_support",
        "name": "청년 월세지원",
        "category": "월세 보조금",
        "fit": ["청년", "월세", "반전세"],
        "required": ["age", "region", "income", "is_homeless", "monthly_rent"],
        "conditions": {"age_min": 19, "age_max": 39, "housing_types": ["월세", "반전세"]},
        "why": "청년이면서 월세로 거주 중이거나 거주 예정이라 매칭되었습니다.",
        "official_check": ["거주지 지자체 청년월세지원 공고문", "해당 연도 소득/재산 기준표", "중복수급 제한 여부(타 주거급여 등)"],
        "official_links": [
            {"name": "복지로 (정부 복지서비스 통합검색)", "url": "https://www.bokjiro.go.kr"},
            {"name": "마이홈포털 (국토부 주거지원 통합)", "url": "https://www.myhome.go.kr"},
        ],
        "caution": "지자체별로 명칭·지원금액·나이 기준(만 34세/39세 등)이 다릅니다. 반드시 관할 지자체 공고로 재확인하세요.",
    },
    {
        "id": "youth_deposit_loan",
        "name": "청년 전월세 보증금 대출 지원",
        "category": "대출/보증",
        "fit": ["청년", "월세", "전세", "반전세"],
        "required": ["age", "income", "deposit", "is_homeless"],
        "conditions": {
            "age_min": 19,
            "age_max": 34,
            "housing_types": ["월세", "전세", "반전세"],
            "requires_homeless": True,
        },
        "why": "청년 전월세 보증금 마련이 필요한 상황으로 보여 매칭되었습니다.",
        "official_check": ["주택도시기금 청년전용 버팀목전세자금 요건", "은행별 취급 조건", "무주택 세대주 요건 충족 여부"],
        "official_links": [
            {"name": "주택도시기금 (청년전용 버팀목전세자금)", "url": "https://nhuf.molit.go.kr"},
        ],
        "caution": "만 나이 기준(주로 19~34세)과 세대주 요건이 엄격합니다. 세대주가 아니면 대상이 아닐 수 있어요.",
    },
    {
        "id": "sme_youth_deposit_loan",
        "name": "중소기업취업청년 전월세보증금대출",
        "category": "대출/보증",
        "fit": ["청년", "월세", "전세"],
        "required": ["age", "income", "deposit"],
        "conditions": {"age_min": 19, "age_max": 34},
        "why": "청년 재직자 대상 저금리 보증금 대출 후보로 매칭되었습니다.",
        "official_check": ["중소·중견기업 재직 여부 증빙", "연소득 기준(공고 기준 최신화 필요)", "보증금 한도 기준"],
        "official_links": [
            {"name": "주택도시기금 (중소기업취업청년 전월세보증금대출)", "url": "https://nhuf.molit.go.kr"},
        ],
        "caution": "중소·중견기업 재직자만 대상입니다. 재직 여부에 따라 대상 자체가 갈릴 수 있어요.",
    },
    {
        "id": "buttmok_jeonse_loan",
        "name": "버팀목 전세자금대출",
        "category": "대출/보증",
        "fit": ["전세"],
        "required": ["income", "deposit", "is_homeless"],
        "conditions": {
            "housing_types": ["전세"],
            "requires_homeless": True,
        },
        "why": "전세 거주 예정이라 일반 서민 전세자금 대출 후보로 매칭되었습니다.",
        "official_check": ["부부합산 연소득 기준", "무주택 세대주 요건", "임차보증금 한도"],
        "official_links": [
            {"name": "주택도시기금 (버팀목전세자금대출)", "url": "https://nhuf.molit.go.kr"},
        ],
        "caution": "소득/자산 기준이 매년 조정됩니다. 최신 기준은 주택도시기금 공식 안내로 확인하세요.",
    },
    {
        "id": "newlywed_jeonse_loan",
        "name": "신혼부부 전세자금대출",
        "category": "대출/보증",
        "fit": ["신혼", "전세"],
        "required": ["marital_status", "income", "deposit", "is_homeless"],
        "conditions": {
            "marital": "married_or_planning",
            "housing_types": ["전세"],
            "requires_homeless": True,
        },
        "why": "신혼(혹은 결혼 예정)이면서 전세 거주 예정이라 매칭되었습니다.",
        "official_check": ["혼인관계증명서 요건(혼인신고일 기준)", "부부합산 소득 기준", "보증금 한도"],
        "official_links": [
            {"name": "주택도시기금 (신혼부부전용 버팀목전세자금)", "url": "https://nhuf.molit.go.kr"},
        ],
        "caution": "결혼 예정자는 혼인신고 전/후 요건이 다를 수 있습니다. 시점을 은행에 명확히 확인하세요.",
    },
    {
        "id": "newlywed_public_housing",
        "name": "신혼희망타운/공공임대 확인",
        "category": "공공주택 공급",
        "fit": ["신혼"],
        "required": ["marital_status", "region", "income"],
        "conditions": {"marital": "married_or_planning"},
        "why": "신혼부부 대상 공공주택 공급 제도 확인이 필요해 후보로 안내드립니다.",
        "official_check": ["LH/SH 청약 공고", "청약통장 가입기간·납입횟수 요건", "자산 기준"],
        "official_links": [
            {"name": "청약홈 (LH/SH 공공주택 청약)", "url": "https://www.applyhome.co.kr"},
            {"name": "LH 한국토지주택공사", "url": "https://www.lh.or.kr"},
            {"name": "SH 서울주택도시공사", "url": "https://www.i-sh.co.kr"},
        ],
        "caution": "청약 경쟁률이 높고 지역별 공급 일정이 다릅니다. 상시 신청이 아니라 공고 시기를 확인해야 합니다.",
    },
    {
        "id": "seoul_youth_moving_support",
        "name": "서울 청년 이사비/중개보수 지원",
        "category": "이사비 지원",
        "fit": ["청년", "이사"],
        "required": ["age", "region", "move_date", "income"],
        "conditions": {"age_min": 19, "age_max": 39, "region_scope": ["서울"]},
        "why": "서울 거주 청년의 이사 관련 비용 지원 후보로 매칭되었습니다.",
        "official_check": ["서울시 또는 자치구 공고", "연간 지원 예산 소진 여부", "신청 기간"],
        "official_links": [
            {"name": "청년몽땅정보통 (서울시 청년정책 포털)", "url": "https://youth.seoul.go.kr"},
        ],
        "caution": "서울시/자치구 예산에 따라 연중 조기 마감될 수 있습니다. 이사 전에 미리 공고를 확인하세요.",
    },
    {
        "id": "local_gov_housing_cost_support",
        "name": "지자체 주거비 지원",
        "category": "지자체 자체 지원",
        "fit": ["청년", "월세", "이사", "신혼"],
        "required": ["region", "age", "income"],
        "conditions": {},
        "why": "거주(예정) 지역의 지자체 자체 주거비 지원 제도 확인이 필요해 후보로 안내드립니다.",
        "official_check": ["시/군/구청 홈페이지 고시공고", "관할 주민센터 문의"],
        "official_links": [
            {"name": "복지로 (정부 복지서비스 통합검색)", "url": "https://www.bokjiro.go.kr"},
            {"name": "마이홈포털 (국토부 주거지원 통합)", "url": "https://www.myhome.go.kr"},
        ],
        "caution": "지자체마다 제도명과 유무 자체가 다릅니다. 국토부 제도와 별개로 반드시 지자체 공고를 확인하세요. 이 링크들은 전국 공통 포털이며, 개별 지자체 공고는 해당 시/군/구청 홈페이지에서 별도 확인이 필요합니다.",
    },
    {
        "id": "jeonse_deposit_return_guarantee",
        "name": "전세보증금 반환보증",
        "category": "보증 상품",
        "fit": ["전세"],
        "required": ["deposit", "contract_status"],
        "conditions": {"housing_types": ["전세"]},
        "why": "전세 거주(예정)로, 보증금 미반환 리스크에 대비한 보증 상품 후보로 안내드립니다.",
        "official_check": ["HUG/HF/SGI 보증 가입 조건", "가입 가능 기한(계약 기간 내 일정 시점까지)", "보증료율"],
        "official_links": [
            {"name": "HUG 주택도시보증공사", "url": "https://www.khug.or.kr"},
        ],
        "caution": "계약 갱신 시점이나 임대인 동의 여부에 따라 가입 가능 여부가 달라질 수 있습니다. HF(주택금융공사), SGI서울보증 등 취급기관마다 조건이 달라 비교가 필요합니다.",
    },
    {
        "id": "housing_benefit_welfare",
        "name": "주거급여",
        "category": "복지 급여",
        "fit": ["월세", "전세", "반전세"],
        "required": ["income", "region", "housing_type"],
        "conditions": {},
        "why": "소득 수준에 따라 기초생활보장제도 내 주거급여 대상 여부 확인이 필요해 후보로 안내드립니다.",
        "official_check": ["기준 중위소득 대비 소득인정액 기준(매년 변경)", "부양의무자 기준 폐지 여부 최신 공고", "임대차계약서 등록 여부"],
        "official_links": [
            {"name": "복지로 (주거급여 신청/자가진단)", "url": "https://www.bokjiro.go.kr"},
            {"name": "마이홈포털 (주거급여 안내)", "url": "https://www.myhome.go.kr"},
        ],
        "caution": "소득인정액 산정 방식이 복잡해 정확한 대상 여부는 주민센터 상담이 가장 정확합니다.",
    },
]


def _evaluate_benefit(profile: dict[str, Any], benefit: dict[str, Any]) -> dict[str, Any]:
    conditions = benefit["conditions"]
    hard_fail_reasons: list[str] = []

    age = profile.get("age")
    if age is not None:
        if conditions.get("age_min") is not None and age < conditions["age_min"]:
            hard_fail_reasons.append(f"나이 기준({conditions['age_min']}세 이상)보다 어린 것으로 확인됩니다.")
        if conditions.get("age_max") is not None and age > conditions["age_max"]:
            hard_fail_reasons.append(f"나이 기준({conditions['age_max']}세 이하)을 초과한 것으로 확인됩니다.")

    housing_type = profile.get("housing_type")
    allowed_types = conditions.get("housing_types")
    if housing_type and allowed_types and housing_type not in allowed_types:
        hard_fail_reasons.append(f"주거 형태({housing_type})가 이 제도의 대상({'/'.join(allowed_types)})과 다릅니다.")

    if conditions.get("marital") and profile.get("marital_status") and profile["marital_status"] != conditions["marital"]:
        hard_fail_reasons.append("혼인 여부 조건(신혼/결혼 예정)에 해당하지 않는 것으로 확인됩니다.")

    region = profile.get("region")
    region_scope = conditions.get("region_scope")
    if region and region_scope and region not in region_scope:
        hard_fail_reasons.append(f"운영 지역({'/'.join(region_scope)}) 밖인 것으로 확인됩니다.")

    if conditions.get("requires_homeless") and profile.get("is_homeless") is False:
        hard_fail_reasons.append("무주택 조건을 충족하지 않는 것으로 확인됩니다.")

    missing = [
        field
        for field in benefit["required"]
        if _is_missing(profile.get(field))
    ]
    missing_labels = [REQUIRED_FIELD_LABELS.get(f, f) for f in missing]

    if hard_fail_reasons:
        status = "현재 조건상 어려움"
    elif not missing:
        status = "가능성 높음"
    else:
        status = "추가 확인 필요"

    return {
        "id": benefit["id"],
        "name": benefit["name"],
        "category": benefit["category"],
        "status": status,
        "why_matched": benefit["why"],
        "concerns": hard_fail_reasons,
        "missing_info": missing_labels,
        "official_check": benefit["official_check"],
        "official_links": benefit.get("official_links", []),
        "caution": benefit["caution"],
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False, "idempotentHint": True}


# ---------------------------------------------------------------------------
# 온통청년(youthcenter.go.kr) 청년정책 오픈API 연동
# 공식 스펙: https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc (청년정책API)
# 요청 URL: https://www.youthcenter.go.kr/go/ythip/getPlcy
# ---------------------------------------------------------------------------

YOUTHCENTER_API_URL = "https://www.youthcenter.go.kr/go/ythip/getPlcy"

# zipCd는 5자리 법정동코드(시/도 단위)가 필요하다. profile.region(예: "서울")을 코드로 매핑.
REGION_ZIP_CODE = {
    "서울": "11000",
    "부산": "26000",
    "대구": "27000",
    "인천": "28000",
    "광주": "29000",
    "대전": "30000",
    "울산": "31000",
    "세종": "36000",
    "경기": "41000",
    "강원": "42000",
    "충북": "43000",
    "충남": "44000",
    "전북": "45000",
    "전남": "46000",
    "경북": "47000",
    "경남": "48000",
    "제주": "50000",
}


def _extract_policy_items(data: Any) -> list[dict[str, Any]]:
    """
    rtnType=json 응답에서 정책 리스트를 꺼낸다.
    공식 문서는 XML 스키마(<youthPolicyList><item>...)만 명시하고 있어,
    json 응답의 정확한 루트 키는 실제 호출 결과로 확인이 필요하다.
    여러 후보 키를 순서대로 시도해 방어적으로 파싱한다.
    """
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("youthPolicyList", "result", "resultList", "list", "youthPolicy"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_policy_items(value)
                if nested:
                    return nested
    return []


def _parse_apply_period(raw: str | None) -> dict[str, Any]:
    """'20260401 ~ 20260414' 형식을 ISO 날짜로 변환하고 현재 접수 상태를 판단한다."""
    if not raw or not raw.strip():
        return {"start": None, "end": None, "status": "상시/수시 또는 기간 정보 없음 (공식 링크 확인 필요)"}

    match = re.match(r"(\d{8})\s*~\s*(\d{8})", raw.strip())
    if not match:
        return {"start": None, "end": None, "status": "기간 형식 확인 필요 (원본: " + raw + ")"}

    try:
        start = datetime.strptime(match.group(1), "%Y%m%d").date()
        end = datetime.strptime(match.group(2), "%Y%m%d").date()
    except ValueError:
        return {"start": None, "end": None, "status": "기간 형식 확인 필요 (원본: " + raw + ")"}

    today = date.today()
    if today < start:
        status = f"접수 예정 (D-{(start - today).days})"
    elif today > end:
        status = "접수 마감"
    else:
        status = f"접수 중 (마감까지 D-{(end - today).days})"

    return {"start": start.isoformat(), "end": end.isoformat(), "status": status}


def _trim(value: Any) -> str | None:
    """앞뒤 공백/줄바꿈을 정리하고 빈 문자열은 None으로 변환."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text if text else None

def _sanitize_api_error(error: Exception) -> str:
    """외부 API 오류 메시지에서 인증키와 URL 파라미터를 제거한다."""

    message = str(error)

    message = re.sub(
        r"(?i)(apiKeyNm|openApiVlak|serviceKey)=([^&\s'\"]+)",
        r"\1=***",
        message,
    )

    # URL 전체가 불필요하게 노출되는 것도 방지
    message = re.sub(
        r"https?://[^\s'\"]+",
        "[외부 API URL 숨김]",
        message,
    )

    return message


def _format_policy_item(item: dict[str, Any]) -> dict[str, Any]:
    """공식 출력결과 필드명(plcyNo, plcyNm 등) 기준으로 사용하기 쉬운 형태로 정리."""
    period = _parse_apply_period(item.get("aplyYmd"))
    apply_url = _trim(item.get("aplyUrlAddr"))

    return {
        "id": item.get("plcyNo"),
        "name": _trim(item.get("plcyNm")),
        "category": _trim(item.get("mclsfNm")) or _trim(item.get("lclsfNm")),
        "description": _trim(item.get("plcyExplnCn")),
        "support_content": _trim(item.get("plcySprtCn")),
        "agency": _trim(item.get("sprvsnInstCdNm")),
        "apply_period_raw": item.get("aplyYmd"),
        "apply_start": period["start"],
        "apply_end": period["end"],
        "apply_status": period["status"],
        "apply_method": _trim(item.get("plcyAplyMthdCn")),
        "apply_url": apply_url,
        "reference_urls": [u for u in [_trim(item.get("refUrlAddr1")), _trim(item.get("refUrlAddr2"))] if u],
        "age_min": item.get("sprtTrgtMinAge"),
        "age_max": item.get("sprtTrgtMaxAge"),
        # 아래 두 코드값은 온통청년 자체 코드정의서(엑셀) 기준 원본 코드다.
        # 공식 매핑표를 확보하지 못해 임의로 해석하지 않고 원본 그대로 전달한다.
        "marital_status_code_raw": item.get("mrgSttsCd"),
        "income_condition_code_raw": item.get("earnCndSeCd"),
        "income_min_manwon": item.get("earnMinAmt"),
        "income_max_manwon": item.get("earnMaxAmt"),
        "additional_conditions": _trim(item.get("addAplyQlfcCndCn")),
    }


def _dedupe_policies(policies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """정부 데이터가 동일 정책을 여러 번(갱신/재공고 등) 내려주는 경우가 있어
    이름+주관기관+신청기간이 모두 같으면 같은 정책으로 보고 하나만 남긴다."""
    seen: set[tuple[Any, ...]] = set()
    deduped = []
    for p in policies:
        fingerprint = (p.get("name"), p.get("agency"), p.get("apply_period_raw"))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(p)
    return deduped


def _static_youth_fallback(keyword: str) -> list[dict[str, Any]]:
    return [
        {
            "name": b["name"],
            "category": b["category"],
            "official_check": b["official_check"],
            "official_links": b.get("official_links", []),
        }
        for b in BENEFITS
        if "청년" in b["fit"]
    ]


@mcp.tool(annotations=READ_ONLY)
def search_official_youth_policy(
    keyword: str = "주거",
    region: str | None = None,
    mid_category: str | None = None,
) -> dict[str, Any]:
    """온통청년(youthcenter.go.kr) 청년정책 오픈API를 실시간으로 조회해 공식 청년정책 후보를 가져온다.
    API 키(YOUTHCENTER_API_KEY 환경변수)가 없거나 호출이 실패하면 자체 보유 정적 데이터로 자동 폴백한다.
    keyword는 plcyKywdNm(정책키워드명)으로, region은 '서울' 같은 시/도명으로 전달하면 zipCd로 변환된다."""

    api_key = os.environ.get("YOUTHCENTER_API_KEY")
    if not api_key:
        return {
            "summary": "실시간 조회용 API 키(YOUTHCENTER_API_KEY)가 설정되지 않아 자체 보유 데이터로 안내합니다.",
            "source": "static_fallback_no_key",
            "policies": _static_youth_fallback(keyword),
            "next_actions": ["서버 환경변수에 YOUTHCENTER_API_KEY를 설정하면 실시간 조회로 전환됩니다."],
            "caution": "실시간 공식 데이터가 아닙니다. 최신 여부는 공식 링크에서 직접 확인하세요.",
        }

    params: dict[str, Any] = {
        "apiKeyNm": api_key,
        "pageNum": 1,
        "pageSize": 10,
        "rtnType": "json",
        "plcyKywdNm": keyword,
    }
    if region:
        zip_code = REGION_ZIP_CODE.get(region)
        if zip_code:
            params["zipCd"] = zip_code
    if mid_category:
        params["mclsfNm"] = mid_category

    try:
        resp = httpx.get(YOUTHCENTER_API_URL, params=params, timeout=6.0)
        resp.raise_for_status()
        data = resp.json()
        raw_items = _extract_policy_items(data)
        policies = _dedupe_policies([_format_policy_item(item) for item in raw_items])

        if not policies:
            return {
                "summary": f"'{keyword}' 관련 실시간 정책을 찾지 못해 자체 보유 데이터로 대체합니다.",
                "source": "static_fallback_empty_result",
                "policies": _static_youth_fallback(keyword),
                "caution": "실시간 API 응답이 비어 있었습니다. 키워드를 바꿔 다시 시도해볼 수 있습니다.",
            }

        return {
            "summary": f"온통청년 API에서 '{keyword}' 관련 정책 {len(policies)}건을 실시간으로 확인했습니다.",
            "source": "youthcenter_live_api",
            "policies": policies,
            "next_actions": ["각 정책의 apply_url에서 최신 신청 조건과 기간을 다시 확인하세요."],
            "caution": "실시간 공식 데이터입니다. marital_status_code_raw/income_condition_code_raw는 온통청년 자체 코드로, 공식 코드정의서 없이는 의미를 단정할 수 없어 원본 그대로 제공합니다. apply_status는 서버 현재 날짜 기준 자동 계산값이니 최종 확인은 apply_url에서 하세요.",
        }

    except Exception as exc:
        return {
            "summary": "실시간 조회에 실패해 자체 보유 데이터로 대체합니다.",
            "source": "static_fallback_on_error",
            "error_type": type(exc).__name__,
            "policies": _static_youth_fallback(keyword),
            "caution": "실시간 공식 데이터가 아닙니다. 최신 여부는 공식 링크에서 직접 확인하세요.",
        }


# ---------------------------------------------------------------------------
# 국토교통부(공공데이터포털) 아파트 전월세 실거래가 API 연동
# 공식 스펙: https://www.data.go.kr/data/15126474/openapi.do
# 요청 URL: https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent
# 주의: 이 API는 '아파트' 거래만 다룬다. 원룸/오피스텔/빌라(다세대) 전월세는 포함되지 않는다.
# ---------------------------------------------------------------------------

MOLIT_RENT_API_URLS = {
    "아파트": "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
    "오피스텔": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
    "연립다세대": "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
    "단독다가구": "https://apis.data.go.kr/1613000/RTMSDataSvcSHRent/getRTMSDataSvcSHRent",
}

# 사용자가 실제로 쓸 법한 일상 용어 -> 공식 API 유형 매핑.
# '원룸/투룸/자취방' 등은 건물 유형이 아니라 방 구조를 가리키는 말이라 특정 유형 하나로
# 단정할 수 없으므로, 오피스텔/연립다세대/단독다가구 세 유형을 모두 조회해 합쳐서 보여준다.
COLLOQUIAL_PROPERTY_TYPE_MAP: dict[str, list[str]] = {
    "아파트": ["아파트"],
    "오피스텔": ["오피스텔"],
    "오피스텔형원룸": ["오피스텔"],
    "빌라": ["연립다세대"],
    "연립": ["연립다세대"],
    "연립주택": ["연립다세대"],
    "다세대": ["연립다세대"],
    "다세대주택": ["연립다세대"],
    "연립다세대": ["연립다세대"],
    "다가구": ["단독다가구"],
    "다가구주택": ["단독다가구"],
    "단독": ["단독다가구"],
    "단독주택": ["단독다가구"],
    "단독다가구": ["단독다가구"],
    "원룸": ["오피스텔", "연립다세대", "단독다가구"],
    "원룸텔": ["오피스텔", "연립다세대", "단독다가구"],
    "투룸": ["오피스텔", "연립다세대", "단독다가구"],
    "쓰리룸": ["연립다세대", "단독다가구"],
    "자취방": ["오피스텔", "연립다세대", "단독다가구"],
    "자취집": ["오피스텔", "연립다세대", "단독다가구"],
    "자취": ["오피스텔", "연립다세대", "단독다가구"],
    "복층": ["오피스텔", "연립다세대", "단독다가구"],
    "복층원룸": ["오피스텔", "연립다세대", "단독다가구"],
    "테라스하우스": ["연립다세대", "단독다가구"],
}

# 확정일자 신고 대상이 아니라 이 API들로 조회 자체가 불가능한 주거 형태.
UNSUPPORTED_PROPERTY_TERMS = {"고시원", "쉐어하우스", "셰어하우스", "하숙", "게스트하우스", "코리빙"}


def _resolve_property_types(raw_term: str) -> tuple[list[str], str | None]:
    """일상 용어를 실제 조회 가능한 API 유형 목록으로 변환.
    반환값: (조회할 유형 리스트, 안내 메시지 또는 None)"""
    term = re.sub(r"\s+", "", raw_term)

    if term in UNSUPPORTED_PROPERTY_TERMS:
        return [], (
            f"'{raw_term}'은(는) 국토부 실거래가 신고 대상이 아니라(확정일자 신고 체계 밖) 이 API로 조회할 수 없습니다."
        )

    resolved = COLLOQUIAL_PROPERTY_TYPE_MAP.get(term)
    if resolved:
        return resolved, None

    return [], (
        f"'{raw_term}'을(를) 인식하지 못했습니다. 아파트/오피스텔/빌라(연립다세대)/다가구(단독다가구)/원룸 중 하나로 다시 말씀해주세요."
    )

# LAWD_CD(법정동코드 앞 5자리, 시군구 단위). 우선 서울 25개 자치구만 지원.
# 다른 지역은 VWORLD 국가중점데이터API > 법정동정보 조회로 확장 가능.
SEOUL_DISTRICT_LAWD_CD = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
    "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
    "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
    "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740",
}


def _recent_deal_ymd(months_back: int = 1) -> str:
    """실거래 신고는 지연 반영되므로 기본적으로 지난달(YYYYMM)을 조회 대상으로 삼는다."""
    today = date.today()
    year, month = today.year, today.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return f"{year}{month:02d}"


def _xml_to_dict(element: ET.Element) -> Any:
    """단순 XML 트리를 중첩 dict로 변환. item이 여러 개면 list로 묶는다."""
    children = list(element)
    if not children:
        return (element.text or "").strip()

    result: dict[str, Any] = {}
    for child in children:
        value = _xml_to_dict(child)
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result


def _molit_items(data: Any) -> list[dict[str, Any]]:
    """공공데이터포털 표준 응답(header/body/items/item)에서 거래 목록을 꺼낸다.
    거래가 1건이면 item이 dict, 여러 건이면 list로 오는 흔한 케이스를 모두 처리."""
    try:
        item = data["response"]["body"]["items"]["item"]
    except (KeyError, TypeError):
        try:
            item = data["body"]["items"]["item"]
        except (KeyError, TypeError):
            return []
    if item is None:
        return []
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return item
    return []


def _format_market_item(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        deposit_manwon = int(str(item.get("deposit", "0")).replace(",", "").strip())
        monthly_rent_manwon = int(str(item.get("monthlyRent", "0")).replace(",", "").strip())
    except (TypeError, ValueError):
        return None

    # 건물명 필드명이 유형별로 다르다: 아파트=aptNm, 오피스텔=offiNm, 연립다세대=mhouseNm.
    # 단독/다가구는 건물명 자체가 없다(지번만 일부 공개).
    building_name = _trim(item.get("aptNm")) or _trim(item.get("offiNm")) or _trim(item.get("mhouseNm"))

    # 면적 필드명도 다르다: 대부분 excluUseAr(전용면적)이지만 단독/다가구는 totalFloorAr(연면적)만 있다.
    area = _trim(item.get("excluUseAr")) or _trim(item.get("totalFloorAr"))

    # 층수 필드는 단독/다가구에는 없다(건물 전체 또는 호실 단위라 층 개념이 명확하지 않음).
    floor = _trim(item.get("floor"))

    return {
        "building_name": building_name,
        "dong": _trim(item.get("umdNm")),
        "area_m2": area,
        "floor": floor,
        "deal_date": f"{item.get('dealYear')}-{str(item.get('dealMonth')).zfill(2)}-{str(item.get('dealDay')).zfill(2)}",
        # 이 API는 전세/월세를 한 데이터셋에 함께 제공한다. monthlyRent가 0이면 전세 거래다.
        "deal_type": "전세" if monthly_rent_manwon == 0 else "월세",
        "deposit_won": deposit_manwon * 10_000,
        "monthly_rent_won": monthly_rent_manwon * 10_000,
    }



def _molit_total_count(data: Any) -> int | None:
    """body.totalCount를 안전하게 꺼낸다. 실제 전체 거래 건수(잘림 여부 판단용)."""
    try:
        body = data.get("response", data).get("body", {})
        total = body.get("totalCount")
        return int(total) if total is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _fetch_deals_page(
    lawd_cd: str, resolved_type: str, deal_ymd: str, api_key: str, page_no: int, num_rows: int
) -> tuple[list[dict[str, Any]] | None, int | None, dict[str, Any] | None]:
    """단일 페이지 호출. (거래 리스트, 전체건수, None) 또는 (None, None, 에러dict) 반환."""
    api_url = MOLIT_RENT_API_URLS[resolved_type]
    try:
        resp = httpx.get(
            api_url,
            params={"LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd, "serviceKey": api_key, "numOfRows": num_rows, "pageNo": page_no},
            timeout=10.0,
        )
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError:
            root = ET.fromstring(resp.text)
            data = _xml_to_dict(root)
            data = {"response": data} if "body" not in data else data

        result_code = None
        try:
            result_code = data.get("response", data).get("header", {}).get("resultCode")
        except AttributeError:
            pass
        if result_code and result_code not in ("00", "000"):
            error_msg = data.get("response", data).get("header", {}).get("resultMsg", "알 수 없는 오류")
            return None, None, {
                "summary": f"{resolved_type} API가 오류를 반환했습니다: {error_msg}",
                "source": "api_error",
                "result_code": result_code,
                "next_actions": ["serviceKey(Decoding 키)가 정확한지, 활용신청이 승인 상태인지 확인해보세요."],
                "caution": "인증키 오류(SERVICE_KEY_IS_NOT_REGISTERED_ERROR 등)인 경우 Decoding 키를 사용했는지 확인하세요.",
            }

        raw_items = _molit_items(data)
        total_count = _molit_total_count(data)
        deals = [d for d in (_format_market_item(i) for i in raw_items) if d]
        for d in deals:
            d["property_type"] = resolved_type
        return deals, total_count, None

    except Exception as exc:  # noqa: BLE001
        return None, None, {
            "summary": f"{resolved_type} 실시간 시세 조회에 실패했습니다.",
            "source": "error",
            "error": str(exc),
            "caution": "API 키가 유효한지, 아직 승인 대기 중은 아닌지 확인해보세요.",
        }


# 전체 수집 시 안전장치: 유형당 최대 이만큼만 페이지네이션 (무한 호출 방지)
MAX_FULL_FETCH_RECORDS = 2000
FULL_FETCH_PAGE_SIZE = 500


def _fetch_deals_for_type(
    district: str, lawd_cd: str, resolved_type: str, deal_ymd: str, api_key: str, fetch_all: bool = False
) -> tuple[list[dict[str, Any]] | None, int | None, dict[str, Any] | None]:
    """단일 API 유형에 대해 실거래가를 조회. fetch_all=False면 최대 200건만 빠르게,
    fetch_all=True면 totalCount를 다 채울 때까지(안전 상한 내에서) 페이지네이션한다.
    반환: (거래 리스트, 전체건수, None) 또는 (None, None, 에러dict)"""

    if not fetch_all:
        return _fetch_deals_page(lawd_cd, resolved_type, deal_ymd, api_key, page_no=1, num_rows=200)

    deals, total_count, error = _fetch_deals_page(
        lawd_cd, resolved_type, deal_ymd, api_key, page_no=1, num_rows=FULL_FETCH_PAGE_SIZE
    )
    if error:
        return None, None, error
    deals = deals or []

    if total_count is None:
        return deals, total_count, None

    page_no = 2
    while len(deals) < total_count and len(deals) < MAX_FULL_FETCH_RECORDS:
        page_deals, _, page_error = _fetch_deals_page(
            lawd_cd, resolved_type, deal_ymd, api_key, page_no=page_no, num_rows=FULL_FETCH_PAGE_SIZE
        )
        if page_error or not page_deals:
            break
        deals.extend(page_deals)
        page_no += 1

    return deals, total_count, None


@mcp.tool(annotations=READ_ONLY)
def check_market_rent(
    district: str,
    property_type: str,
    year_month: str | None = None,
    user_deposit: int | None = None,
    user_monthly_rent: int | None = None,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """국토교통부 전월세 실거래가 오픈API(공공데이터포털)를 실시간으로 조회해 해당 자치구의 최근 거래
    평균 보증금/월세를 계산하고, user_deposit/user_monthly_rent(원 단위)와 비교해 시세 대비 높은지/낮은지
    안내한다. 현재는 서울 25개 자치구만 지원한다.
    property_type은 사용자가 실제로 말한 표현을 그대로 전달하면 된다 — '아파트', '오피스텔', '빌라',
    '연립다세대', '다가구', '단독다가구', '원룸', '투룸', '자취방' 등 일상 용어를 내부적으로 실제 API
    유형에 매핑한다. '원룸'처럼 건물 유형이 특정되지 않는 표현은 오피스텔/연립다세대/단독다가구 세 유형을
    모두 조회해 합쳐서 보여준다. 고시원/셰어하우스처럼 확정일자 신고 대상이 아닌 주거 형태는 조회할 수 없다.
    기본(fetch_all=False)은 유형당 최대 200건만 빠르게 가져오며, 거래량이 많은 지역에서는 일부만 반영된
    평균일 수 있다는 안내(truncated_types)가 함께 온다. 이 경우 먼저 사용자에게 "전체 데이터를 다 가져오면
    시간이 더 걸리는데 그래도 원하는지" 물어보고, 사용자가 그렇다고 답하면 fetch_all=True로 다시 호출해
    전체 거래를 페이지네이션으로 모두 가져온다(유형당 최대 2000건 안전 상한). 사용자에게 먼저 묻지 않고
    바로 fetch_all=True로 호출하지 않는다.
    district는 '관악구'처럼 구 이름만 전달한다."""

    api_key = os.environ.get("PUBLIC_DATA_API_KEY")
    if not api_key:
        return {
            "summary": "실시간 시세 조회용 API 키(PUBLIC_DATA_API_KEY)가 설정되지 않았습니다.",
            "source": "no_key",
            "next_actions": ["공공데이터포털(data.go.kr)에서 국토교통부 전월세 실거래가 자료 API를 신청하고 서버 환경변수에 PUBLIC_DATA_API_KEY를 설정하세요."],
            "caution": "시세 비교 없이도 다른 기능은 정상 이용 가능합니다.",
        }

    lawd_cd = SEOUL_DISTRICT_LAWD_CD.get(district)
    if not lawd_cd:
        return {
            "summary": f"'{district}'는 현재 지원 지역(서울 25개 자치구) 밖이라 시세 비교를 제공할 수 없습니다.",
            "source": "unsupported_region",
            "next_actions": ["서울 소재 자치구명을 입력해보세요. (예: 관악구, 강남구)"],
            "caution": "타 지역은 법정동코드 매핑이 아직 없어 추후 확장이 필요합니다.",
        }

    resolved_types, guidance = _resolve_property_types(property_type)
    if not resolved_types:
        return {
            "summary": guidance,
            "source": "unrecognized_property_type",
            "next_actions": ["아파트/오피스텔/빌라/다가구/원룸 등으로 다시 시도해보세요."],
            "caution": "정확하지 않은 유형으로 임의 추정하지 않고 확인을 요청하는 것입니다.",
        }

    deal_ymd = year_month or _recent_deal_ymd()
    if not re.fullmatch(r"\d{6}", deal_ymd):
        return {
            "summary": f"'{year_month}'는 올바른 계약년월 형식이 아닙니다.",
            "source": "invalid_year_month",
            "next_actions": ["year_month를 YYYYMM 6자리 형식으로 다시 전달해주세요. 예: 2026-06 이사면 '202606'"],
            "caution": "형식이 맞지 않으면 정확한 조회가 불가능합니다.",
        }

    all_deals: list[dict[str, Any]] = []
    truncated_types: list[dict[str, Any]] = []
    for rtype in resolved_types:
        deals, total_count, error = _fetch_deals_for_type(district, lawd_cd, rtype, deal_ymd, api_key, fetch_all=fetch_all)
        if error:
            # 여러 유형 중 하나라도 API 자체 오류(인증 등)면 즉시 에러를 반환한다.
            return error
        deals = deals or []
        all_deals.extend(deals)
        # numOfRows=200으로 요청했는데 실제 전체 건수가 그보다 많으면 일부만 반영된 것이다.
        if total_count is not None and total_count > len(deals):
            truncated_types.append({"property_type": rtype, "shown": len(deals), "total": total_count})

    if not all_deals:
        return {
            "summary": f"{district}의 {deal_ymd} {'/'.join(resolved_types)} 전월세 거래 데이터를 찾지 못했습니다.",
            "source": "empty_result",
            "property_types_searched": resolved_types,
            "next_actions": ["year_month를 다른 달로 바꿔 다시 시도해보세요."],
            "caution": "해당 월에 신고된 거래가 없거나 아직 반영되지 않았을 수 있습니다.",
        }

    jeonse_deals = [d for d in all_deals if d["deal_type"] == "전세"]
    wolse_deals = [d for d in all_deals if d["deal_type"] == "월세"]

    def _avg(items: list[dict[str, Any]], key: str) -> int:
        return sum(d[key] for d in items) // len(items) if items else 0

    jeonse_avg_deposit = _avg(jeonse_deals, "deposit_won")
    wolse_avg_deposit = _avg(wolse_deals, "deposit_won")
    wolse_avg_rent = _avg(wolse_deals, "monthly_rent_won")

    comparison = None
    if user_monthly_rent:
        comparison = {"basis": "월세 거래 기준"}
        if user_deposit is not None and wolse_avg_deposit > 0:
            diff_pct = round((user_deposit - wolse_avg_deposit) / wolse_avg_deposit * 100, 1)
            comparison["deposit_vs_avg_pct"] = diff_pct
            comparison["deposit_note"] = (
                f"월세 거래 평균 보증금보다 약 {abs(diff_pct)}% {'높습니다' if diff_pct > 0 else '낮습니다'}" if abs(diff_pct) >= 5 else "월세 거래 평균 보증금과 비슷한 수준입니다"
            )
        if wolse_avg_rent > 0:
            diff_pct = round((user_monthly_rent - wolse_avg_rent) / wolse_avg_rent * 100, 1)
            comparison["monthly_rent_vs_avg_pct"] = diff_pct
            comparison["monthly_rent_note"] = (
                f"평균보다 약 {abs(diff_pct)}% {'높습니다' if diff_pct > 0 else '낮습니다'}" if abs(diff_pct) >= 5 else "평균과 비슷한 수준입니다"
            )
        if not wolse_deals:
            comparison["note"] = f"{deal_ymd}에는 {district}에 해당 유형 월세 거래 표본이 없어 비교가 어렵습니다. 다른 달로 다시 시도해보세요."
    elif user_deposit is not None:
        comparison = {"basis": "전세 거래 기준"}
        if jeonse_avg_deposit > 0:
            diff_pct = round((user_deposit - jeonse_avg_deposit) / jeonse_avg_deposit * 100, 1)
            comparison["deposit_vs_avg_pct"] = diff_pct
            comparison["deposit_note"] = (
                f"전세 거래 평균 보증금보다 약 {abs(diff_pct)}% {'높습니다' if diff_pct > 0 else '낮습니다'}" if abs(diff_pct) >= 5 else "전세 거래 평균 보증금과 비슷한 수준입니다"
            )
        else:
            comparison["note"] = f"{deal_ymd}에는 {district}에 해당 유형 전세 거래 표본이 없어 비교가 어렵습니다. 다른 달로 다시 시도해보세요."

    type_breakdown = {
        rtype: sum(1 for d in all_deals if d["property_type"] == rtype) for rtype in resolved_types
    }

    return {
        "summary": (
            f"{district} {deal_ymd} '{property_type}'({'/'.join(resolved_types)}) 거래 {len(all_deals)}건 "
            f"(전세 {len(jeonse_deals)}건 / 월세 {len(wolse_deals)}건) 기준 — "
            f"전세 평균 보증금 {jeonse_avg_deposit:,}원, 월세 평균 보증금 {wolse_avg_deposit:,}원 / 평균 월세 {wolse_avg_rent:,}원입니다."
        ),
        "source": "molit_live_api",
        "property_type_input": property_type,
        "property_types_searched": resolved_types,
        "type_breakdown": type_breakdown,
        "district": district,
        "year_month": deal_ymd,
        "deal_count": len(all_deals),
        "jeonse_count": len(jeonse_deals),
        "wolse_count": len(wolse_deals),
        "jeonse_avg_deposit_won": jeonse_avg_deposit,
        "wolse_avg_deposit_won": wolse_avg_deposit,
        "wolse_avg_monthly_rent_won": wolse_avg_rent,
        "sample_jeonse_deals": jeonse_deals[:3],
        "sample_wolse_deals": wolse_deals[:3],
        "comparison": comparison,
        "truncated_types": truncated_types,
        "fetch_all_used": fetch_all,
        "next_actions": (
            ["district, property_type, year_month를 바꿔 다른 조건과 비교해볼 수 있습니다."]
            + (
                []
                if fetch_all or not truncated_types
                else [
                    "일부 유형은 거래량이 많아 200건만 반영됐습니다. 사용자가 전체 데이터를 원하면(응답이 더 걸릴 수 있음을 안내한 뒤) fetch_all=true로 다시 호출하세요."
                ]
            )
        ),
        "caution": (
            f"'{property_type}' 입력을 {'/'.join(resolved_types)} 유형으로 해석해 조회했습니다. "
            "전세와 월세는 성격이 달라 평균을 분리해서 계산했습니다."
            + (
                " 주의: " + ", ".join(f"{t['property_type']}({t['shown']}/{t['total']}건만 반영)" for t in truncated_types)
                + " — 거래량이 많아 일부만 조회되어 평균이 실제와 다를 수 있습니다."
                if truncated_types
                else ""
            )
        ),
    }


# ---------------------------------------------------------------------------
# 한국토지주택공사(LH) 임대주택단지 조회 서비스 연동
# 공식 스펙: https://www.data.go.kr/data/15059475/openapi.do
# 요청 URL: https://apis.data.go.kr/B552555/lhLeaseInfo1/lhLeaseInfo1
# 주의: 이 API의 임대보증금(LS_GMY)/월임대료(RFE)는 이미 '원' 단위로 온다 (국토부 API처럼 만원 단위가 아님).
# ---------------------------------------------------------------------------

LH_LEASE_API_URL = "https://apis.data.go.kr/B552555/lhLeaseInfo1/lhLeaseInfo1"

# 공식 활용가이드 기준 정확한 지역코드(CNP_CD). 표준 법정동코드와 다른 값들이 있으니 주의
# (세종은 5자리, 강원/전북은 특별자치도 개편에 따른 별도 코드, 광주는 전남과 통합된 코드).
LH_REGION_CODE = {
    "서울": "11",
    "광주": "12", "전남": "12",  # 전남광주통합특별시
    "부산": "26",
    "대구": "27",
    "인천": "28",
    "대전": "30",
    "울산": "31",
    "세종": "36110",
    "경기": "41",
    "충북": "43",
    "충남": "44",
    "경북": "47",
    "경남": "48",
    "제주": "50",
    "강원": "51",  # 강원특별자치도
    "전북": "52",  # 전북특별자치도
}

# 공식 활용가이드 기준 정확한 공급유형코드(SPL_TP_CD). 정확히 일치하면 서버 쪽 필터로 사용해
# 데이터 전송량을 줄이고, 그 외 표현은 응답을 받은 뒤 이름으로 부분일치 필터링한다.
LH_SUPPLY_TYPE_CODE = {
    "국민임대": "07",
    "공공임대": "08",
    "영구임대": "09",
    "행복주택": "10",
    "장기전세": "11",
    "매입임대": "13",
    "전세임대": "17",
}


def _lh_result_code(data: Any) -> tuple[str | None, str | None]:
    """실제 응답은 [{'dsSch':...}, {'resHeader':[{'SS_CODE':...,'RS_DTTM':...}], 'dsList':[...]}] 형태다.
    resHeader에서 SS_CODE를 꺼낸다. (성공은 'Y')"""
    if not isinstance(data, list):
        return None, None
    for element in data:
        if isinstance(element, dict) and "resHeader" in element:
            header = element["resHeader"]
            if isinstance(header, list) and header:
                return header[0].get("SS_CODE"), header[0].get("RS_DTTM")
    return None, None


def _lh_items(data: Any) -> list[dict[str, Any]]:
    """LH API 응답에서 실제 단지 목록(dsList)을 꺼낸다.
    공식 응답 구조: [{'dsSch': [...]}, {'resHeader': [...], 'dsList': [ ...단지들... ]}]"""
    if isinstance(data, list):
        for element in data:
            if isinstance(element, dict) and "dsList" in element:
                ds_list = element["dsList"]
                if isinstance(ds_list, list):
                    return ds_list
        # 혹시 리스트 자체가 이미 단지 항목들의 리스트인 변형 케이스
        if data and all(isinstance(e, dict) and ("SBD_LGO_NM" in e or "ARA_NM" in e) for e in data):
            return data
        return []

    if isinstance(data, dict):
        if "SBD_LGO_NM" in data or "ARA_NM" in data:
            return [data]
        for key in ("dsList", "resultList", "items", "item", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _lh_items(value)
                if nested:
                    return nested

    return []

def _optional_int(value: Any) -> int | None:
    """비어 있는 숫자 필드는 None으로 유지한다."""

    if value is None:
        return None

    text = str(value).replace(",", "").strip()

    if not text or text.lower() in {"-", "null", "none"}:
        return None

    try:
        return int(text)
    except ValueError:
        return None

def _format_lh_complex(item: dict[str, Any]) -> dict[str, Any] | None:
    deposit = _optional_int(item.get("LS_GMY"))
    monthly_rent = _optional_int(item.get("RFE"))
    total_households = _optional_int(item.get("SUM_HSH_CNT"))
    households = _optional_int(item.get("HSH_CNT"))

    if total_households is None:
        total_households = 0

    if households is None:
        households = 0

    all_cnt_raw = str(item.get("ALL_CNT", "")).replace(",", "").strip()
    all_cnt = int(all_cnt_raw) if all_cnt_raw.isdigit() else None

    return {
        "region": _trim(item.get("ARA_NM")),
        "supply_type": _trim(item.get("AIS_TP_CD_NM")),
        "complex_name": _trim(item.get("SBD_LGO_NM")),
        "total_households": total_households,
        "area_m2": _trim(item.get("DDO_AR")),
        "households_this_type": households,
        # 이미 원 단위. 만원 단위 변환 절대 하지 않는다 (국토부 API와 다름).
        "deposit_won": deposit,
        "monthly_rent_won": monthly_rent,
        "rent_information_status": (
            "원본 API 값이 모두 0원입니다. 실제 임대조건은 최신 공고에서 확인이 필요합니다."
            if deposit == 0 and monthly_rent == 0
            else (
                "원본 API에 임대금액 정보가 없습니다."
                if deposit is None and monthly_rent is None
                else "임대금액 정보 제공"
            )
        ),
        "first_move_in_ym": _trim(item.get("MVIN_XPC_YM")),
        "_all_cnt": all_cnt,  # 페이지네이션 전체건수 계산용 내부 필드
    }


@mcp.tool(annotations=READ_ONLY)
def search_lh_rental_complexes(
    region: str,
    supply_type_keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """한국토지주택공사(LH) 임대주택단지 조회 오픈API(공공데이터포털)를 실시간으로 조회해
    해당 시/도의 공공임대주택 단지 목록(단지명, 공급유형, 총세대수, 전용면적, 임대보증금, 월임대료 등)을
    가져온다. region은 '서울', '경기' 같은 시/도명이며 서울 자치구뿐 아니라 전국 시/도를 지원한다.
    supply_type_keyword로 '국민임대', '공공임대', '영구임대', '행복주택', '장기전세', '매입임대',
    '전세임대' 중 정확히 일치하는 값을 주면 API에 SPL_TP_CD로 함께 전달하지만, 실제로는 이 API가
    해당 파라미터를 무시하는 것이 확인되어 응답을 받은 뒤 이름으로 항상 다시 필터링한다. 그 외
    표현(예: '신혼희망타운')은 응답을 받은 뒤 이름에 포함되는지로 느슨하게 필터링한다. 지정하지 않으면
    전체 공급유형을 반환한다."""

    api_key = os.environ.get("PUBLIC_DATA_API_KEY")
    if not api_key:
        return {
            "summary": "실시간 LH 임대주택단지 조회용 API 키(PUBLIC_DATA_API_KEY)가 설정되지 않았습니다.",
            "source": "no_key",
            "next_actions": ["공공데이터포털(data.go.kr)에서 '한국토지주택공사_임대주택단지 조회 서비스' API를 신청하고 서버 환경변수에 PUBLIC_DATA_API_KEY를 설정하세요."],
            "caution": "이 기능 없이도 다른 기능은 정상 이용 가능합니다.",
        }

    # page/page_size에 0, 음수, 비정상적으로 큰 값이 들어와도 안전한 범위로 보정한다.
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    region_code = LH_REGION_CODE.get(region)
    if not region_code:
        return {
            "summary": f"'{region}'을(를) 인식하지 못했습니다.",
            "source": "unsupported_region",
            "next_actions": [f"지원 지역: {', '.join(sorted(set(LH_REGION_CODE.keys())))}"],
            "caution": "시/도 단위 이름으로 입력해주세요 (예: '서울', '경기').",
        }

    params: dict[str, Any] = {"ServiceKey": api_key, "PG_SZ": page_size, "PAGE": page, "CNP_CD": region_code}
    server_side_filtered = False
    if supply_type_keyword and supply_type_keyword in LH_SUPPLY_TYPE_CODE:
        params["SPL_TP_CD"] = LH_SUPPLY_TYPE_CODE[supply_type_keyword]
        server_side_filtered = True

    try:
        resp = httpx.get(LH_LEASE_API_URL, params=params, timeout=8.0)
        resp.raise_for_status()
        data = resp.json()

        ss_code, _ = _lh_result_code(data)
        if ss_code and ss_code != "Y":
            return {
                "summary": f"API가 오류를 반환했습니다 (SS_CODE={ss_code}).",
                "source": "api_error",
                "next_actions": ["serviceKey(Decoding 키)가 정확한지, 활용신청이 승인 상태인지 확인해보세요."],
                "caution": "SS_CODE가 'Y'가 아니면 정상 응답이 아닙니다.",
            }

        raw_items = _lh_items(data)
        complexes = [c for c in (_format_lh_complex(i) for i in raw_items) if c]

        total_count = next((c["_all_cnt"] for c in complexes if c.get("_all_cnt")), None)
        for c in complexes:
            c.pop("_all_cnt", None)

        pre_filter_count = len(complexes)
        # 서버(SPL_TP_CD)가 필터를 실제로 적용했는지 신뢰할 수 없어(실제 테스트에서 무시되는 것이 확인됨),
        # server_side_filtered 여부와 무관하게 항상 클라이언트에서 이름으로 다시 필터링한다.
        if supply_type_keyword:
            complexes = [c for c in complexes if c["supply_type"] and supply_type_keyword in c["supply_type"]]

        if not complexes:
            debug_info: dict[str, Any] = {
                "raw_items_found": len(raw_items),
                "complexes_before_type_filter": pre_filter_count,
                "server_side_filtered": server_side_filtered,
            }
            if raw_items and pre_filter_count and supply_type_keyword:
                reason = f"'{supply_type_keyword}' 조건에 맞는 단지가 없습니다. (필터 전 {pre_filter_count}건 있었음)"
            elif raw_items:
                reason = "항목은 찾았지만 필드 파싱에 실패했습니다."
            else:
                reason = "해당 조건(지역/페이지)에 데이터가 없습니다."

            return {
                "summary": f"{region}에서 조건에 맞는 LH 임대주택단지를 찾지 못했습니다. ({reason})",
                "source": "empty_result",
                "debug": debug_info,
                "next_actions": ["supply_type_keyword를 빼고 다시 시도하거나 page를 1로 설정해보세요."],
                "caution": "이 지역/조건에는 데이터가 없을 수 있습니다.",
            }

        next_actions = ["특정 단지의 상세 신청 조건은 LH청약센터(apply.lh.or.kr)에서 재확인하세요."]
        truncation_note = ""
        if total_count is not None and total_count > len(complexes) and not supply_type_keyword:
            next_actions.append(f"전체 {total_count}건 중 {len(complexes)}건만 조회되었습니다. page를 늘려 더 볼 수 있습니다.")
            truncation_note = f" (전체 {total_count}건 중 {len(complexes)}건 표시, page={page})"

        return {
            "summary": f"{region} 지역에서 LH 임대주택단지 {len(complexes)}건을 확인했습니다.{truncation_note}",
            "source": "lh_live_api",
            "region": region,
            "page": page,
            "total_count": total_count,
            "complexes": complexes,
            "next_actions": next_actions,
            "caution": "임대보증금/월임대료는 단지·주택형별 대표값이며, 실제 신청 시점의 최신 공고 조건과 다를 수 있습니다. first_move_in_ym이 비정상적인 값(예: 미래 연도)으로 나오는 경우가 있는데, 이는 LH 원본 데이터의 이상값으로 보이며 저희 파싱 문제가 아닙니다.",
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "summary": "LH 임대주택단지 실시간 조회에 실패했습니다.",
            "source": "error",
            "error": str(exc),
            "caution": "API 키가 유효한지, 아직 승인 대기 중은 아닌지 확인해보세요.",
        }


@mcp.tool(annotations=READ_ONLY)
def parse_housing_profile(user_text: str) -> dict[str, Any]:
    """집생활 내비: 자연어 문장에서 세입자의 이사·주거지원 관련 조건을 추출한다.
    상대적 날짜(다음 달 20일), 한글 숫자 금액(월세 칠십, 보증금 천만원), 구/군(서울 관악구),
    연봉(연봉 3000), 계약 단계(계약 전 / 계약함) 등을 인식한다."""

    region = next((r for r in ["서울", "경기", "인천", "부산", "대구", "대전", "광주", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"] if r in user_text), None)

    district = None
    if region:
        d_match = re.search(rf"{region}\s*([가-힣]{{1,4}}(?:구|군|시))", user_text)
        if d_match:
            district = d_match.group(1)

    housing_type = next((h for h in ["반전세", "월세", "전세", "자가"] if h in user_text), None)

    age_match = re.search(r"만\s*(\d{1,2})\s*세|(\d{1,2})\s*살|(\d{1,2})\s*세", user_text)
    age = next((int(x) for x in age_match.groups() if x), None) if age_match else None

    marital_status = None
    if any(word in user_text for word in ["신혼", "결혼 예정", "결혼할", "혼인"]):
        marital_status = "married_or_planning"
    elif "미혼" in user_text:
        marital_status = "single"

    before_signing_pattern = re.search(
        r"계약(?:은|는|이|가)?\s*"
        r"(?:아직\s*)?"
        r"(?:안\s*(?:했|한|함|했어|했습니다)|전)",
        user_text,
    )

    if (
        before_signing_pattern
        or any(
            keyword in user_text
            for keyword in [
                "아직 계약하지",
                "계약하지 않았",
                "계약하지 않은",
                "계약 전",
                "계약전",
                "미계약",
            ]
        )
    ):
        contract_status = "before_signing"

    elif any(
        keyword in user_text
        for keyword in [
            "계약함",
            "계약했",
            "계약을 했",
            "계약서 작성",
            "계약 완료",
            "계약체결",
            "계약 체결",
        ]
    ):
        contract_status = "signed"

    elif "계약" in user_text:
        contract_status = "in_progress"

    else:
        contract_status = None

    is_homeless = True if "무주택" in user_text else (False if "유주택" in user_text else None)

    annual_income = _money_to_won(user_text, "연봉")
    monthly_income = (
        _money_to_won(user_text, "소득")
        or _money_to_won(user_text, "월급")
        or _money_to_won(user_text, "급여")
        or _money_to_won(user_text, "월수입")
        or _money_to_won(user_text, "실수령액")
        or _money_to_won(user_text, "실수령")
    )
    income = monthly_income or (int(annual_income / 12) if annual_income else None)

    profile = {
        "age": age,
        "region": region,
        "district": district,
        "housing_type": housing_type,
        "move_date": _parse_move_date(user_text),
        "deposit": _money_to_won(user_text, "보증금"),
        "monthly_rent": _money_to_won(user_text, "월세"),
        "income": income,
        "income_annual": annual_income,
        "marital_status": marital_status,
        "is_homeless": is_homeless,
        "contract_status": contract_status,
    }
    profile["missing_fields"] = _profile_missing(profile)

    return {
        "summary": _profile_summary_text(profile),
        "detected_profile": profile,
        "missing_fields": profile["missing_fields"],
        "next_actions": (
            ["ask_missing_info로 부족한 정보를 질문 형태로 정리해보세요.", "match_housing_benefits로 혜택 후보를 확인해보세요."]
            if any(profile.get(k) for k in ["age", "region", "housing_type"])
            else ["나이, 지역, 주거 형태(월세/전세) 정도만 더 알려주시면 후보를 정리해드릴 수 있어요."]
        ),
        "caution": (
            "이 정보는 사용자가 입력한 문장에서 자동으로 추정한 값이라 오탐이 있을 수 있습니다. 서버에 저장되지 않습니다."
            + (
                " 참고: 실거래가 시세 비교(check_market_rent)는 현재 서울 25개 자치구만 지원합니다."
                if region and region != "서울"
                else ""
            )
        ),
    }


@mcp.tool(annotations=READ_ONLY)
def _unwrap_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """MCP 연결 과정에서 한 번 더 감싸진 profile 객체를 해제한다."""

    current = profile

    # 최대 3번까지만 풀어 무한 중첩을 방지한다.
    for _ in range(3):
        nested = None

        for key in ("profile", "detected_profile"):
            value = current.get(key)

            if isinstance(value, dict):
                nested = value
                break

        if nested is None:
            break

        current = nested

    return current
def ask_missing_info(profile: dict[str, Any]) -> dict[str, Any]:
    """집생활 내비: 주거지원 대상 여부를 더 정확히 판단하기 위해 부족한 정보를 질문 형태로 만든다."""
    profile = _unwrap_profile(profile)
    question_map = {
        "age": "나이가 어떻게 돼?",
        "region": "어느 지역 집으로 이사해?",
        "housing_type": "월세, 전세, 반전세 중 어떤 형태야?",
        "move_date": "이사 예정일은 언제야?",
        "deposit": "보증금은 얼마야?",
        "monthly_rent": "매달 내는 월세 금액은 얼마야?",
        "income": "월 소득(또는 연봉)을 대략 알려줄 수 있어?",
        "marital_status": "미혼, 신혼, 결혼 예정 중 어디에 가까워?",
        "is_homeless": "현재 무주택자야?",
        "contract_status": "계약 전이야, 계약 진행 중이야, 아니면 계약서 작성 완료했어?",
    }
    missing = list(profile.get("missing_fields") or _profile_missing(profile))
    questions = [question_map[x] for x in missing if x in question_map]

    # 시세 비교(check_market_rent)에는 구/군 단위 정보가 필요하다.
    # region은 있는데 district가 없으면 missing_fields엔 안 잡히지만 별도로 물어본다.
    if profile.get("region") and not profile.get("district"):
        missing.append("district")
        questions.append("정확히 몇 구(군)야? (시세 비교에 필요해)")

    return {
        "summary": f"정확한 매칭을 위해 {len(questions)}개 정보가 더 필요합니다." if questions else "핵심 정보는 충분히 확인되었습니다.",
        "missing_fields": missing,
        "questions": questions,
        "next_actions": ["답변을 받은 뒤 parse_housing_profile로 다시 통합하거나, match_housing_benefits에 바로 반영해보세요."],
        "caution": "이 질문들은 진단이 아니라 정보 수집 목적입니다. 답변은 저장되지 않습니다.",
    }


@mcp.tool(annotations=READ_ONLY)
def match_housing_benefits(profile: dict[str, Any]) -> dict[str, Any]:
    """집생활 내비: 주거지원 제도 후보를 3단계 상태(가능성 높음 / 추가 확인 필요 / 현재 조건상 어려움)로
    매칭하고, 각 제도마다 매칭 이유·부족한 정보·공식 확인처·주의사항을 함께 제공한다. 최종 법적 자격
    판정은 절대 하지 않는다. 청년(만 39세 이하) 프로필이면 온통청년 공식 API(search_official_youth_policy)에서
    실시간 청년정책 후보도 함께 가져온다. 신혼(예정 포함) 프로필이고 지역이 확인되면 LH 공공임대주택
    단지 목록(search_lh_rental_complexes, 신혼희망타운 키워드)도 실시간으로 함께 가져온다."""
    profile = _unwrap_profile(profile)

    tags = set()
    is_youth = profile.get("age") is not None and profile["age"] <= 39
    if is_youth:
        tags.add("청년")
    ht = profile.get("housing_type")
    if ht == "반전세":
        tags.add("월세")
        tags.add("전세")
    elif ht:
        tags.add(ht)
    is_newlywed = profile.get("marital_status") == "married_or_planning"
    if is_newlywed:
        tags.add("신혼")
    if profile.get("move_date"):
        tags.add("이사")

    evaluated = []
    for benefit in BENEFITS:
        if not tags.intersection(benefit["fit"]):
            continue
        evaluated.append(_evaluate_benefit(profile, benefit))

    order = {"가능성 높음": 0, "추가 확인 필요": 1, "현재 조건상 어려움": 2}
    evaluated.sort(key=lambda x: order[x["status"]])

    high = [b["name"] for b in evaluated if b["status"] == "가능성 높음"]
    review = [b["name"] for b in evaluated if b["status"] == "추가 확인 필요"]

    if evaluated:
        summary = f"{len(evaluated)}개 제도가 후보로 확인되었습니다."
        if high:
            summary += f" 그중 '{', '.join(high)}'은(는) 현재 정보 기준으로 가능성이 높아 보입니다."
    else:
        summary = "현재 입력된 정보만으로는 매칭되는 제도가 없습니다. 나이/지역/주거 형태를 조금 더 알려주세요."

    next_actions = []
    if review:
        next_actions.append("ask_missing_info로 '추가 확인 필요' 항목의 부족한 정보를 질문해보세요.")
    if high or review:
        next_actions.append("각 제도의 official_check 항목을 실제 공고문에서 재확인하세요.")
    if not evaluated:
        next_actions.append("parse_housing_profile로 나이/지역/주거형태를 먼저 파악해보세요.")

    result: dict[str, Any] = {
        "summary": summary,
        "matches": evaluated,
        "next_actions": next_actions,
        "caution": "본 결과는 공식 심사가 아닌 후보 안내입니다. 최신 공고, 소득/자산 기준, 세대구성, 계약 조건에 따라 실제 결과는 달라질 수 있습니다.",
    }

    # 청년(만 39세 이하)이면 온통청년 실시간 API도 함께 조회해 정적 매칭을 보강한다.
    # 키워드가 너무 좁으면(예: '월세') 결과가 0건일 수 있어, 더 넓은 키워드로 자동 재시도한다.
    if is_youth:
        candidate_keywords = []
        if ht:
            candidate_keywords.append(ht)
        candidate_keywords.append("주거")

        live = None
        live_keyword_used = candidate_keywords[0]
        for kw in candidate_keywords:
            live = search_official_youth_policy(keyword=kw, region=profile.get("region"))
            live_keyword_used = kw
            if live.get("source") == "youthcenter_live_api" and live.get("policies"):
                break  # 결과를 찾았으면 더 시도하지 않는다

        live_policies = (live or {}).get("policies", [])[:5]
        live_source = (live or {}).get("source")

        if live_source == "youthcenter_live_api":
            # 실시간 API 결과: 신청 상태/URL 등 API 전용 필드로 정리
            display_policies = [
                {
                    "name": p.get("name"),
                    "agency": p.get("agency"),
                    "support_content": p.get("support_content"),
                    "apply_status": p.get("apply_status"),
                    "apply_url": p.get("apply_url"),
                }
                for p in live_policies
            ]
        else:
            # 폴백(정적 데이터): 원본 필드(name/category/official_check)를 그대로 보존
            display_policies = live_policies

        result["live_youth_policies"] = {
            "source": live_source,
            "keyword_used": live_keyword_used,
            "policies": display_policies,
            "caution": (live or {}).get("caution"),
        }
        if live_source == "youthcenter_live_api" and live_policies:
            result["summary"] += f" 추가로 온통청년 실시간 API에서 '{live_keyword_used}' 관련 청년정책 {len(live_policies)}건도 함께 확인했습니다."
            result["next_actions"].append("live_youth_policies의 apply_url에서 실시간 정책의 최신 신청 조건을 확인하세요.")

    # 신혼(예정 포함)이고 지역이 시/도 단위로 확인되면 LH 공공임대주택 단지도 함께 조회한다.
    region = profile.get("region")
    lh_region = None
    if region:
        # profile.region은 '서울' 같은 시/도명이거나 이미 LH_REGION_CODE 키와 동일한 값일 수 있다.
        if region in LH_REGION_CODE:
            lh_region = region
    if is_newlywed and lh_region:
        lh = search_lh_rental_complexes(region=lh_region, supply_type_keyword="신혼희망타운")
        lh_complexes = lh.get("complexes", [])[:5]
        lh_source = lh.get("source")

        # '신혼희망타운' 자체 공급유형이 없으면(느슨한 이름필터라 흔함) 전체 유형으로 한 번 더 시도한다.
        if lh_source == "empty_result":
            lh = search_lh_rental_complexes(region=lh_region)
            lh_complexes = lh.get("complexes", [])[:5]
            lh_source = lh.get("source")

        result["live_lh_complexes"] = {
            "source": lh_source,
            "region_used": lh_region,
            "complexes": lh_complexes,
            "caution": lh.get("caution"),
        }
        if lh_source == "lh_live_api" and lh_complexes:
            result["summary"] += f" 추가로 LH 임대주택단지 실시간 API에서 {lh_region} 지역 공공임대 단지 {len(lh_complexes)}건도 함께 확인했습니다."
            result["next_actions"].append("live_lh_complexes는 LH청약센터(apply.lh.or.kr)에서 최신 신청 조건을 다시 확인하세요.")

    return result


@mcp.tool(annotations=READ_ONLY)
def generate_moving_timeline(move_date: str, housing_type: str = "월세") -> dict[str, Any]:
    """집생활 내비: 전세/월세 세입자를 위한 이사 D-day 체크리스트를 생성한다.
    move_date는 ISO 날짜 형식(YYYY-MM-DD)이어야 하며, parse_housing_profile의 출력값을 그대로 넣을 수 있다."""

    cleaned_move_date = str(move_date).strip().strip('"').strip("'").strip()

    try:
        base = datetime.strptime(cleaned_move_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {
            "summary": f"'{cleaned_move_date}'를 날짜로 인식하지 못했습니다.",
            "timeline": [],
            "next_actions": ["move_date를 YYYY-MM-DD 형식으로 다시 전달해주세요. 예: 2026-08-03"],
            "caution": "날짜 형식이 올바르지 않으면 정확한 D-day 계산이 불가능합니다.",
        }

    timeline = [
        {"label": "D-30", "date": (base - timedelta(days=30)).isoformat(), "tasks": ["기존 계약 종료일 확인", "보증금 반환 일정 확인", "이사비 견적 비교", "주거지원 신청 조건 확인"]},
        {"label": "D-14", "date": (base - timedelta(days=14)).isoformat(), "tasks": ["이사업체 예약", "인터넷 이전 신청", "도시가스 이전/해지 예약", "관리비 정산 방식 확인"]},
        {"label": "D-7", "date": (base - timedelta(days=7)).isoformat(), "tasks": ["전입신고 준비", "확정일자 준비", "입주 전 하자 체크리스트 준비", "주소 변경할 서비스 목록 정리"]},
        {"label": "D-Day", "date": base.isoformat(), "tasks": ["계량기 사진 촬영", "집 상태 사진/영상 기록", "열쇠와 도어락 확인", "관리비와 공과금 정산 확인"]},
        {"label": "D+1~7", "date": f"{(base + timedelta(days=1)).isoformat()}~{(base + timedelta(days=7)).isoformat()}", "tasks": ["전입신고", "확정일자", "우편물/금융/통신 주소 변경", "주거지원 신청 마감일 확인"]},
    ]

    days_from_today = (base - date.today()).days
    date_sanity_note = ""
    if days_from_today < 0:
        date_sanity_note = " 참고: 입력하신 이사일이 오늘보다 과거입니다. 날짜를 다시 확인해보세요."
    elif days_from_today > 730:
        date_sanity_note = " 참고: 입력하신 이사일이 2년 이상 남았습니다. 너무 이른 계획이라면 날짜를 다시 확인해보세요."

    return {
        "summary": f"{move_date} 이사 기준 D-30부터 D+7까지의 체크리스트를 정리했습니다.{date_sanity_note}",
        "move_date": move_date,
        "housing_type": housing_type,
        "timeline": timeline,
        "next_actions": ["check_contract_tasks로 계약 단계별 세부 체크리스트도 함께 확인해보세요."],
        "caution": "실제 전입신고/확정일자 처리 가능 시점은 지자체·전산 상황에 따라 다를 수 있습니다.",
    }


@mcp.tool(annotations=READ_ONLY)
def check_contract_tasks(
    stage: Literal["계약전", "입주", "갱신", "퇴거", "보증금반환"] = "입주",
    housing_type: str = "월세",
) -> dict[str, Any]:
    """집생활 내비: 계약 전/입주/갱신/퇴거/보증금반환 단계별 실무 체크리스트를 제공한다."""
    normalized = re.sub(r"\s+", "", stage)
    tasks_map = {
        "계약전": ["등기부등본 확인", "임대인 정보 확인", "보증금/월세/관리비 항목 확인", "특약사항 확인", "전입신고와 확정일자 가능 여부 확인"],
        "입주": ["계량기 사진 촬영", "하자 사진/영상 기록", "열쇠와 도어락 확인", "관리비 부과 기준 확인", "전입신고와 확정일자 처리"],
        "갱신": ["계약갱신청구권 행사 가능 여부 확인(최초 계약 후 1회, 계약 종료 6개월~2개월 전 통지)", "증액 상한(5%) 준수 여부 확인", "묵시적 갱신 여부 확인(임대인/임차인 모두 별다른 통지 없었는지)", "갱신 시 새 계약서 작성 여부 및 확정일자 재확인", "임대인 변경(매매) 시 계약 승계 여부 확인"],
        "퇴거": ["퇴거일 사전 통보", "원상복구 범위 확인", "관리비/공과금 정산", "집 상태 사진 기록", "보증금 반환 계좌 전달"],
        "보증금반환": ["반환 예정일 확인", "공제 항목 근거 요청", "입주/퇴거 사진 비교", "정산 내역 문서화", "분쟁 시 공식 상담기관 확인"],
    }
    tasks = tasks_map.get(normalized, tasks_map["입주"])

    return {
        "summary": f"'{stage}' 단계에서 확인할 {len(tasks)}가지 항목입니다.",
        "stage": stage,
        "housing_type": housing_type,
        "tasks": tasks,
        "next_actions": ["필요하면 generate_message_template으로 관련 연락 문장을 바로 만들어보세요."],
        "caution": "분쟁 발생 시에는 이 체크리스트만으로 판단하지 말고 주택임대차분쟁조정위원회 등 공식 기관에 상담하세요.",
    }


MESSAGE_TEMPLATES: dict[str, dict[str, str]] = {
    "보증금반환": {
        "정중하게": "안녕하세요. 이사 일정 때문에 보증금 반환 일정을 미리 확인드리고 싶습니다. 계약 종료일에 맞춰 반환 가능하신지 확인 부탁드립니다. 필요한 정산 항목이 있다면 미리 알려주시면 준비하겠습니다.",
        "단호하게": "안녕하세요. 계약 종료일이 다가와 보증금 반환 일정을 명확히 확인하고자 연락드립니다. 계약서상 종료일에 맞춰 반환해주시기 바라며, 공제 항목이 있다면 근거 자료와 함께 사전에 안내 부탁드립니다.",
        "캐주얼하게": "안녕하세요! 이사 날짜가 다가와서 미리 여쭤봐요. 보증금은 계약 끝나는 날에 맞춰서 돌려주실 수 있을까요? 정산할 게 있으면 미리 알려주시면 저도 준비해둘게요.",
    },
    "하자확인": {
        "정중하게": "안녕하세요. 입주 전 확인 차 연락드립니다. 집 내부 하자나 수리 필요 부분이 있는지 함께 확인하고, 입주일 기준 사진으로 기록해두고 싶습니다.",
        "단호하게": "안녕하세요. 입주 전 하자 여부를 명확히 하기 위해 연락드립니다. 입주일에 임대인(또는 관리소) 입회 하에 하자 상태를 함께 점검하고 사진으로 남기고자 하니 협조 부탁드립니다.",
        "캐주얼하게": "안녕하세요~ 입주 전에 집 상태 한번 같이 확인해봐도 될까요? 혹시 고장 나거나 수리 필요한 부분 있으면 미리 알려주시면 좋을 것 같아요. 입주날 사진도 좀 찍어두려고요!",
    },
    "확정일자": {
        "정중하게": "안녕하세요. 계약 후 전입신고와 확정일자를 진행하려고 합니다. 계약서상 주소와 임대인 정보가 맞는지 한 번만 확인 부탁드립니다.",
        "단호하게": "안녕하세요. 전입신고와 확정일자 절차 진행을 위해 계약서상 주소지와 임대인 정보를 다시 한번 정확히 확인해주시기 바랍니다.",
        "캐주얼하게": "안녕하세요! 전입신고랑 확정일자 받으려고 하는데, 계약서에 있는 주소랑 임대인 정보 한번만 확인해주실 수 있을까요?",
    },
    "이사일정": {
        "정중하게": "안녕하세요. 이사 일정 조율 관련해서 연락드립니다. 예정일은 [날짜]로 생각하고 있는데, 해당 일정으로 진행 가능하실지 확인 부탁드립니다.",
        "단호하게": "안녕하세요. 이사 일정을 확정하고자 연락드립니다. 예정일인 [날짜]로 진행할 예정이니, 문제가 있으시면 미리 말씀해주시기 바랍니다.",
        "캐주얼하게": "안녕하세요~ 이사 날짜 조율하려고 연락드려요! [날짜]로 생각하고 있는데 괜찮으실까요?",
    },
    "수리요청": {
        "정중하게": "안녕하세요. 거주 중 [수리가 필요한 부분]이 발견되어 연락드립니다. 확인 후 수리 일정 조율 가능하실지 여쭤봅니다. 사진 함께 보내드리겠습니다.",
        "단호하게": "안녕하세요. [수리가 필요한 부분]에 대한 수리가 필요한 상황입니다. 생활에 불편이 있어 빠른 확인과 조치 부탁드리며, 조치가 지연될 경우 별도로 안내드리겠습니다.",
        "캐주얼하게": "안녕하세요! 집에 [수리가 필요한 부분]이 좀 이상한 것 같아서 연락드려요. 언제쯤 확인 가능하실까요? 사진 같이 보내드릴게요.",
    },
    "계약연장문의": {
        "정중하게": "안녕하세요. 계약 만료일이 다가와 연장 가능 여부를 미리 여쭤보고자 연락드립니다. 갱신 시 조건(월세/보증금 변동 등)이 있다면 [날짜]쯤까지 함께 안내 부탁드립니다.",
        "단호하게": "안녕하세요. 계약 만료일 전에 갱신 여부를 명확히 하고자 합니다. 연장 가능 여부와 조건을 [날짜]까지 회신 부탁드립니다.",
        "캐주얼하게": "안녕하세요~ 계약 끝나가는데 혹시 연장 가능할까요? [날짜] 전에 조건 바뀌는 거 있으면 미리 알려주시면 감사하겠습니다!",
    },
    "계약해지통보": {
        "정중하게": "안녕하세요. 개인 사정으로 계약을 [날짜]부로 종료하고자 합니다. 계약서상 해지 절차와 필요한 서류를 안내해주시면 그에 맞춰 준비하겠습니다.",
        "단호하게": "안녕하세요. 계약을 [날짜]부로 해지하고자 통보드립니다. 계약서 제O조에 따른 절차를 진행해주시기 바라며, 보증금 반환 일정도 함께 협의 부탁드립니다.",
        "캐주얼하게": "안녕하세요, 저 이번에 사정이 있어서 [날짜]에 계약 종료하려고 하는데, 어떻게 처리하면 될지 알려주실 수 있을까요?",
    },
    "월세감액요청": {
        "정중하게": "안녕하세요. [사유]로 인해 최근 시세와 저희 집 상황을 고려했을 때 월세 조정이 가능한지 한번 여쭤보고 싶어 연락드립니다. 시간 되실 때 편하게 말씀해주시면 감사하겠습니다.",
        "단호하게": "안녕하세요. 주변 시세 및 [사유]를 고려할 때 현재 월세 조정이 필요하다고 판단되어 연락드립니다. 협의 가능하실지 회신 부탁드립니다.",
        "캐주얼하게": "안녕하세요~ [사유] 때문에 그런데 혹시 월세 조금 조정 가능할까 해서 여쭤봐요. 편하실 때 한번 얘기 나눠볼 수 있을까요?",
    },
    "관리비문의": {
        "정중하게": "안녕하세요. 이번 달 관리비 산정 항목이 궁금해 연락드립니다. 세부 내역을 확인할 수 있을지 문의드립니다.",
        "단호하게": "안녕하세요. 관리비 산정 근거가 명확하지 않아 세부 내역서를 요청드립니다. 항목별 상세 자료를 받아볼 수 있을까요?",
        "캐주얼하게": "안녕하세요! 이번 달 관리비 내역이 좀 궁금한데, 세부 항목 좀 알려주실 수 있을까요?",
    },
    "반려동물문의": {
        "정중하게": "안녕하세요. 반려동물과 함께 거주해도 괜찮을지 미리 여쭤보고 싶어 연락드립니다. 크기나 마리 수 등 제한 사항이 있다면 안내 부탁드립니다.",
        "단호하게": "안녕하세요. 반려동물 동반 거주 가능 여부를 계약 전에 명확히 확인하고자 합니다. 가능 여부와 조건을 알려주시기 바랍니다.",
        "캐주얼하게": "안녕하세요~ 혹시 반려동물 키워도 괜찮을까요? 제한되는 부분 있으면 미리 알려주세요!",
    },
    "소음문의": {
        "정중하게": "안녕하세요. 최근 층간소음(또는 공용공간 소음)으로 불편을 겪고 있어 연락드립니다. 확인 및 중재 부탁드릴 수 있을지 여쭤봅니다.",
        "단호하게": "안녕하세요. 지속적인 소음 문제로 생활에 지장이 있어 관리 차원의 확인과 조치를 요청드립니다. 처리 결과를 안내해주시면 감사하겠습니다.",
        "캐주얼하게": "안녕하세요, 요즘 소음 때문에 좀 힘든데 혹시 확인해주실 수 있을까요?",
    },
    "대출서류협조요청": {
        "정중하게": "안녕하세요. 전세자금대출(또는 보증보험 가입) 진행을 위해 임대인 확인 서류가 필요합니다. 가능하시면 [기한]까지 협조 부탁드려도 될까요?",
        "단호하게": "안녕하세요. 대출 실행을 위해 임대인 서류 제출이 필수적입니다. [기한]까지 협조 부탁드리며, 필요한 서류 목록을 함께 안내드리겠습니다.",
        "캐주얼하게": "안녕하세요! 저 전세대출 받으려고 하는데, 임대인분 서류가 좀 필요해서요. [기한] 전에 협조 가능하실까요?",
    },
    "공과금이전문의": {
        "정중하게": "안녕하세요. [날짜]에 이사 예정이라 인터넷/도시가스 이전(또는 해지) 문의드립니다. 신규 설치 및 기존 계약 해지 일정을 안내해주실 수 있을까요?",
        "단호하게": "안녕하세요. [날짜] 이사에 맞춰 인터넷/도시가스 이전을 반드시 완료해야 합니다. 가능한 빠른 일정으로 예약 부탁드립니다.",
        "캐주얼하게": "안녕하세요~ 저 [날짜]에 이사하는데, 인터넷(또는 가스)이전 신청하려고요! 언제로 예약 가능할까요?",
    },
}

PURPOSE_DEFAULT_RECIPIENT: dict[str, str] = {
    "보증금반환": "집주인",
    "하자확인": "집주인",
    "확정일자": "집주인",
    "이사일정": "집주인",
    "수리요청": "집주인",
    "계약연장문의": "집주인",
    "계약해지통보": "집주인",
    "월세감액요청": "집주인",
    "관리비문의": "관리사무소",
    "반려동물문의": "집주인",
    "소음문의": "관리사무소",
    "대출서류협조요청": "집주인",
    "공과금이전문의": "통신사/도시가스 고객센터",
}


@mcp.tool(annotations=READ_ONLY)
def generate_message_template(
    purpose: Literal[
        "보증금반환", "하자확인", "확정일자", "이사일정",
        "수리요청", "계약연장문의", "계약해지통보", "월세감액요청",
        "관리비문의", "반려동물문의", "소음문의", "대출서류협조요청",
        "공과금이전문의",
    ],
    recipient: str | None = None,
    tone: Literal["정중하게", "단호하게", "캐주얼하게"] = "정중하게",
) -> dict[str, Any]:
    """집생활 내비: 집주인·관리사무소·부동산 등에게 보낼 짧은 메시지를 작성한다.
    입주/퇴거, 계약, 수리, 월세 협상, 대출 서류 등 다양한 상황을 다루며,
    상황마다 정중하게/단호하게/캐주얼하게 3가지 톤으로 제공한다."""

    resolved_recipient = recipient or PURPOSE_DEFAULT_RECIPIENT.get(purpose, "집주인")
    message = MESSAGE_TEMPLATES[purpose][tone]
    needs_fill_in = "[" in message

    return {
        "summary": f"{resolved_recipient}에게 보낼 '{purpose}' 문장을 '{tone}' 톤으로 준비했습니다.",
        "recipient": resolved_recipient,
        "tone": tone,
        "purpose": purpose,
        "message": message,
        "next_actions": (
            ["[ ]로 표시된 부분(날짜, 사유 등)을 실제 내용으로 채워 넣어 보내세요."]
            if needs_fill_in
            else ["필요하면 이름, 날짜, 구체적인 항목을 문장에 직접 채워 넣어 보내세요."]
        ),
        "caution": "법적 효력이 필요한 통보(예: 계약 해지 통보)는 문자 대신 내용증명 등 공식 방법을 함께 고려하세요.",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")