"""회원가입·로그인 담당 모듈. CLI 입력과 CSV 입출력은 다른 모듈이 맡는다."""

import hashlib
import hmac
import re
import secrets

from . import validation as v
from .errors import RuleError

ITERATIONS = 600_000
HASH_FORMAT = re.compile(r"pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}")


def hash_password(password):
    """계정마다 새 16바이트 salt를 사용한다. 원문은 반환/저장하지 않는다."""
    v.password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def valid_hash(encoded):
    return HASH_FORMAT.fullmatch(encoded) is not None


def verify_password(password, encoded):
    if not valid_hash(encoded) or not 8 <= len(password) <= 64:
        return False
    try:
        v.ensure_utf8(password)
    except RuleError:
        return False
    _, iterations, salt, expected = encoded.split("$")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(actual.hex(), expected)


def check_new_id(users, candidate):
    v.user_id(candidate)
    if any(u["user_id"].lower() == candidate.lower() for u in users):
        raise RuleError("이미 사용 중인 회원 ID입니다. 다른 ID를 입력해 주세요.")
    return candidate


def make_account(users, user_id, display_name, password, role="member"):
    check_new_id(users, user_id)
    if role not in ("member", "admin"):
        raise RuleError("계정 권한이 올바르지 않습니다.")
    if role == "admin" and any(u["role"] == "admin" for u in users):
        raise RuleError("최초 관리자 등록은 관리자 계정이 없을 때만 가능합니다.")
    return {"user_id": user_id, "display_name": v.display_name(display_name),
            "password_hash": hash_password(password), "role": role, "active": "true"}


def authenticate(users, user_id, password):
    """성공 시 계정 복사본, 실패 시 None. 실패 사유는 CLI에서 구별하지 않는다."""
    user = next((u for u in users if u["user_id"].lower() == user_id.lower()), None)
    if user is None or user["active"] != "true":
        return None
    return dict(user) if verify_password(password, user["password_hash"]) else None
