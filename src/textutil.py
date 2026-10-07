"""공고 본문 텍스트 정리 도구."""

SECTION_MARKERS = ["모집부문", "모집분야", "담당업무", "주요업무", "업무내용",
                   "자격요건", "지원자격", "우대사항"]


def focus_text(text: str, max_chars: int = 6000) -> str:
    """사이트 메뉴·광고 같은 앞부분을 건너뛰고 본문 위주로 잘라낸다.

    첫 번째 섹션 표시(담당업무 등)보다 조금 앞에서 시작해서 경력·학력 같은
    상단 요약 정보도 같이 포함되게 한다. 표시를 못 찾으면 앞에서부터 자른다.
    작은 로컬 모델(3B)은 입력이 길수록 판단이 흐려지기 때문에 길이를 제한한다.
    """
    if not text:
        return ""
    positions = [text.find(m) for m in SECTION_MARKERS if text.find(m) >= 0]
    start = max(0, min(positions) - 800) if positions else 0
    return text[start:start + max_chars]
