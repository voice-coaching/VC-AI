"""H5 expression policy appended to the verified S7 pure helpers at packaging.

Candidate selection, guidance objects and factual evidence remain S7-owned.
This changes expression text only; it is not a new pronunciation diagnosis.
"""

MAX_ACTION_UTF16 = 140
_META_LANGUAGE = re.compile(r"모델|검출|정렬|후보|보류|불확실|대치|확정|판단|기대\s*소리|연습\s*항목|단정")


def _action(value):
    text = _text(value)
    if (text != value or len(text.encode("utf-16-le")) // 2 > MAX_ACTION_UTF16
            or not text.endswith(".")
            or re.search(r"[.!?。！？…\r\n\u2028\u2029]", text[:-1])):
        raise ValueError("H5_ACTION_SENTENCE_LIMIT")
    if _META_LANGUAGE.search(text):
        raise ValueError("H5_ACTION_META_LANGUAGE")
    return text


def concise_action(candidate):
    facts = candidate["facts"]
    word = facts[0]["location"]["word"] if facts else "해당 단어"
    if re.fullmatch(r"[가-힣ㄱ-ㅎㅏ-ㅣ]{1,24}", word) is None or _META_LANGUAGE.search(word):
        word = "해당 단어"
    phone, role = candidate["expectedPhone"], candidate["expectedRole"]
    action = f"‘{word}’ → 소리를 따로 끊지 말고 천천히 이어 말한 뒤 자연스러운 속도로 연결해 보세요."
    # Only known single Korean targets may be quoted in deterministic text.
    if re.fullmatch(r"[가-힣ㄱ-ㅎㅏ-ㅣ]", phone):
        if role == "nucleus":
            action = f"‘{word}’ → ‘{phone}’ 소리를 천천히 낸 뒤 앞뒤 소리에 부드럽게 연결해 보세요."
        elif role == "onset" and phone in "ㅂㅃㅁ":
            action = f"‘{word}’ → ‘{phone}’을 낼 때 두 입술을 가볍게 맞댄 뒤 바로 뒤 모음으로 연결해 보세요."
        elif role == "onset" and phone in "ㅋㅌㅍ":
            action = f"‘{word}’ → ‘{phone}’의 시작에서 숨을 짧게 내보내며 뒤 모음에 붙여 연습해 보세요."
        elif role == "onset" and phone in "ㅅㅆ":
            action = f"‘{word}’ → ‘{phone}’의 마찰음을 짧게 낸 뒤 뒤 모음에 바로 연결해 보세요."
    return _action(action)


def template_rows(view):
    return [dict(candidateId=c["candidateId"], guidanceId=c["guidance"]["guidanceId"],
                 explanation="목표 소리를 단어 안에서 이어 말하는 연습입니다.",
                 action=concise_action(c),
                 practice=c["guidance"]["practice"], selfCheck=c["guidance"]["selfCheck"])
            for c in view["candidates"]]
