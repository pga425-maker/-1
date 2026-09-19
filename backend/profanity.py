"""닉네임 욕설 필터.

외부 라이브러리 없이 동작해야 해서(인터넷 없는 환경) 자체 목록을 쓴다.
완벽한 필터는 불가능하다. 행사장에서 관리자가 강제 퇴장으로 대응할 수 있게
해 두는 것이 실질적인 대비책이다.
"""

from __future__ import annotations

import re
import unicodedata

BANNED = (
    "시발", "씨발", "새끼", "개새", "병신", "지랄", "좆", "썅", "미친놈", "미친년",
    "느금", "니애미", "니미", "창녀", "걸레년", "등신", "찌질", "멍청이",
    "fuck", "shit", "bitch", "asshole", "dick", "pussy", "cunt", "bastard",
    "죽어라", "자살", "강간", "섹스",
)

# 자모 분리·반복·특수문자 삽입으로 우회하는 것을 조금이나마 막는다
_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)

MIN_LENGTH = 1
MAX_LENGTH = 12


def normalize(nickname: str) -> str:
    text = unicodedata.normalize("NFKC", nickname).lower()
    return _STRIP.sub("", text)


def check(nickname: str) -> str | None:
    """문제가 있으면 사유 문자열, 없으면 None 을 돌려준다."""
    stripped = nickname.strip()
    if len(stripped) < MIN_LENGTH:
        return "닉네임을 입력해 주세요"
    if len(stripped) > MAX_LENGTH:
        return f"닉네임은 {MAX_LENGTH}자 이내로 해 주세요"
    if not re.fullmatch(r"[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ ]+", stripped):
        return "한글, 영문, 숫자만 쓸 수 있습니다"
    folded = normalize(stripped)
    for word in BANNED:
        if word in folded:
            return "쓸 수 없는 단어가 들어 있습니다"
    return None
