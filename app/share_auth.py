"""공유에 거는 암호(참여 암호). 화면 코드가 없는 순수 로직이다.

공유 PC 는 공유를 시작할 때 암호를 정할 수 있다(비워 두면 없다). 암호가 있으면
참여하려는 PC 는 암호를 알고 있음을 보여야 메모를 받는다.

통신은 암호화하지 않으므로 암호 자체를 보내지 않는다.

* 공유 PC 는 암호에서 만든 열쇠만 가진다(``derive_key``: PBKDF2). 암호 글자는
  어디에도 적지 않는다.
* 붙을 때마다 서로 한 번 쓰고 버리는 값(nonce)을 주고받고, 열쇠로 그 값의
  증명(HMAC)을 만들어 보낸다. 엿들은 증명을 다시 보내도 통하지 않는다.
* 공유 PC 도 같은 방식으로 자기가 열쇠를 가졌음을 보인다. 참여하는 PC 는 그것을
  확인하고 나서야 받은 메모를 믿는다(공유 PC 인 척하는 상대를 가린다).

참여하는 PC 는 한 번 맞힌 열쇠를 적어 두어 다음부터 묻지 않는다. 공유 PC 가
암호를 바꾸면 소금(salt)이 달라져 적어 둔 열쇠가 맞지 않고, 다시 묻게 된다."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any

ITERATIONS = 200_000
# 상대가 보낸 반복 횟수를 그대로 믿으면 터무니없는 값으로 이 PC 를 붙잡아 둘 수 있다.
MIN_ITERATIONS = 10_000
MAX_ITERATIONS = 2_000_000
SALT_BYTES = 16
KEY_BYTES = 32
NONCE_BYTES = 16
MAX_PASSWORD_LENGTH = 128


def clean_password(text: Any) -> str:
    """입력 칸의 글을 암호로. 빈칸뿐이면 암호가 없는 것이다.

    같은 글자를 다르게 조합해 넣는 입력기가 있어(한글, 가나의 탁점) 한 가지
    꼴로 맞춘다. 앞뒤 빈칸은 쓴 사람이 넣은 것일 수 있으므로 그대로 둔다."""
    if not isinstance(text, str) or not text.strip():
        return ""
    return unicodedata.normalize("NFC", text)[:MAX_PASSWORD_LENGTH]


def derive_key(password: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", clean_password(password).encode("utf-8"),
                               salt, int(iterations), dklen=KEY_BYTES)


def new_nonce() -> str:
    return secrets.token_hex(NONCE_BYTES)


def _proof(key: bytes, role: bytes, *parts: str) -> str:
    body = role + b"\x00" + b"\x00".join(str(p).encode("utf-8") for p in parts)
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def guest_proof(key: bytes, host_nonce: str, guest_nonce: str, nickname: str) -> str:
    """참여하는 PC 가 보내는 증명. 닉네임을 넣어 다른 이름으로 바꿔 쓰지 못하게 한다."""
    return _proof(key, b"guest", host_nonce, guest_nonce, nickname)


def host_proof(key: bytes, host_nonce: str, guest_nonce: str) -> str:
    """공유 PC 가 첫 상태(welcome)에 실어 보내는 증명."""
    return _proof(key, b"host", host_nonce, guest_nonce)


def same(a: Any, b: Any) -> bool:
    return (isinstance(a, str) and isinstance(b, str)
            and hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8")))


@dataclass(frozen=True)
class ShareAuth:
    """암호에서 만든 열쇠와 그것을 만든 값들. 공유 PC 는 자기 것을, 참여하는 PC 는
    붙던 공유 PC 의 것을 적어 둔다."""
    salt: bytes
    key: bytes
    iterations: int = ITERATIONS

    @classmethod
    def from_password(cls, password: str) -> "ShareAuth | None":
        """새 암호로 만든다. 암호가 비어 있으면 None(암호 없이 공유)."""
        password = clean_password(password)
        if not password:
            return None
        salt = secrets.token_bytes(SALT_BYTES)
        return cls(salt=salt, key=derive_key(password, salt, ITERATIONS),
                   iterations=ITERATIONS)

    @classmethod
    def for_challenge(cls, password: str, salt_hex: Any,
                      iterations: Any) -> "ShareAuth | None":
        """공유 PC 가 알려 준 소금으로, 사용자가 넣은 암호의 열쇠를 만든다."""
        salt = _unhex(salt_hex, SALT_BYTES)
        count = _count(iterations)
        password = clean_password(password)
        if salt is None or count is None or not password:
            return None
        return cls(salt=salt, key=derive_key(password, salt, count), iterations=count)

    def to_dict(self) -> dict[str, Any]:
        return {"salt": self.salt.hex(), "key": self.key.hex(),
                "iterations": int(self.iterations)}

    @classmethod
    def from_dict(cls, raw: Any) -> "ShareAuth | None":
        if not isinstance(raw, dict):
            return None
        salt = _unhex(raw.get("salt"), SALT_BYTES)
        key = _unhex(raw.get("key"), KEY_BYTES)
        count = _count(raw.get("iterations"))
        if salt is None or key is None or count is None:
            return None
        return cls(salt=salt, key=key, iterations=count)

    def matches(self, salt_hex: Any, iterations: Any) -> bool:
        """공유 PC 가 지금 쓰는 소금이 이 열쇠를 만든 것과 같은가."""
        return (_unhex(salt_hex, SALT_BYTES) == self.salt
                and _count(iterations) == self.iterations)


def _unhex(value: Any, size: int) -> bytes | None:
    if not isinstance(value, str) or len(value) != size * 2:
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_ITERATIONS <= value <= MAX_ITERATIONS else None


def valid_nonce(value: Any) -> bool:
    return (isinstance(value, str) and 16 <= len(value) <= 128
            and all(c in "0123456789abcdef" for c in value))


__all__ = ["ShareAuth", "clean_password", "derive_key",
           "guest_proof", "host_proof", "new_nonce", "same", "valid_nonce"]
