"""정규식은 문법, 날짜·중복·대출 조건은 별도의 의미 검사로 다룬다."""

import re
import unicodedata
from datetime import date

from .emoji_ranges import EMOJI_RANGES
from .errors import RuleError

KDC = {
    "000": "총류", "100": "철학", "200": "종교", "300": "사회과학",
    "400": "자연과학", "500": "기술과학", "600": "예술", "700": "언어",
    "800": "문학", "900": "역사",
}
USER_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,19}")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
IDS = {"book_id": r"B[0-9]{4}", "copy_id": r"C[0-9]{4}",
       "loan_id": r"L[0-9]{6}"}
ID_LABELS = {"book_id": "도서정보 ID", "copy_id": "권본 ID", "loan_id": "대출 ID"}


def user_id(value):
    if USER_ID.fullmatch(value) is None:
        raise RuleError("회원 ID는 영문자로 시작하는 4~20자의 영문자·숫자·밑줄이어야 합니다.")
    return value


def text_field(value, label, maximum, required=True):
    value = value.strip()
    ensure_utf8(value)
    if (required and not value) or len(value) > maximum:
        span = f"1~{maximum}자" if required else f"{maximum}자 이하"
        raise RuleError(f"{label}은(는) 앞뒤 공백을 제외하고 {span}로 입력해 주세요.")
    return value


def display_name(value):
    return text_field(value, "표시 이름", 40)


def password(value):
    # 앞뒤 공백도 비밀번호의 일부이다. 공백만으로 된 비밀번호만 거부한다.
    ensure_utf8(value)
    if not 8 <= len(value) <= 64 or not value.strip():
        raise RuleError("비밀번호는 8~64자이며 공백만으로 구성할 수 없습니다.")
    return value


def ensure_utf8(value):
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise RuleError("UTF-8로 저장할 수 없는 문자가 포함되어 있습니다.") from None


def identifier(value, field):
    if re.fullmatch(IDS[field], value) is None:
        example = {"book_id": "B0001", "copy_id": "C0001", "loan_id": "L000001"}[field]
        raise RuleError(f"{ID_LABELS[field]} 형식이 올바르지 않습니다. 예: {example}")
    return value


def date_value(value):
    if DATE.fullmatch(value) is None:
        raise RuleError("날짜는 YYYY-MM-DD 형식으로 입력해 주세요.")
    try:
        return date(*map(int, value.split("-")))
    except ValueError:
        raise RuleError("존재하는 날짜를 입력해 주세요. 허용 연도는 0001~9999입니다.") from None


def book_field(field, value):
    value = value.strip()
    if field in ("title", "author", "publisher"):
        label, maximum, required = {
            "title": ("도서명", 100, True), "author": ("저자", 80, True),
            "publisher": ("출판사", 80, False),
        }[field]
        return text_field(value, label, maximum, required)
    if field == "published_year" and value and re.fullmatch(r"[0-9]{4}", value) is None:
        raise RuleError("발행 연도는 비워 두거나 네 자리 숫자로 입력해 주세요.")
    if field == "isbn" and value and re.fullmatch(r"[0-9]{13}", value) is None:
        raise RuleError("ISBN은 하이픈 없이 숫자 13자리로 입력해 주세요.")
    if field == "category" and value not in KDC:
        raise RuleError("올바른 KDC 분류를 선택해 주세요.")
    return value


# 숫자/#/* 단독은 일반 검색어로 허용하되, 키캡 결합과 이모지 구성
# 문자(변형 선택자·ZWJ)는 거부한다. 나머지는 Emoji 16.0 속성표를 쓴다.
EMOJI_SINGLE = {0x200D, 0x20E3, 0xFE0E, 0xFE0F}


def search_query(raw):
    if raw == "":
        return None  # Enter만 입력한 경우 취소 (5.2절의 상세 의미 규칙).
    value = raw.strip()
    if not value:
        raise RuleError("검색어를 입력해 주세요.")
    if len(value) > 100:
        raise RuleError("검색어는 앞뒤 공백을 제외하고 1~100자로 입력해 주세요.")
    for ch in value:
        n = ord(ch)
        emoji = n in EMOJI_SINGLE or any(a <= n <= b for a, b in EMOJI_RANGES)
        hangul = (0x1100 <= n <= 0x11FF or 0x3130 <= n <= 0x318F
                  or 0xA960 <= n <= 0xA97F or 0xAC00 <= n <= 0xD7AF
                  or 0xD7B0 <= n <= 0xD7FF)
        allowed = (ch.isspace() or ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                   or hangul or unicodedata.category(ch)[0] in "PS")
        if ch in "\\|" or emoji or not allowed:
            raise RuleError("허용되지 않은 문자가 포함되어 있습니다. ('\\', '|', 이모지 등)")
    return value


def loan_selection(raw, count):
    value = raw.strip()
    if raw == "":
        return None
    if re.fullmatch(r"[0-9]+", value) is None:
        raise RuleError("숫자만 입력 가능합니다.")
    # int()의 입력 길이 제한과 불필요한 거대 정수 변환을 피한다.
    digits = value.lstrip("0") or "0"
    if digits == "0":
        return None
    if len(digits) > len(str(count)) or int(digits) > count:
        raise RuleError("검색 결과 목록에 없는 번호입니다.")
    return int(digits) - 1
