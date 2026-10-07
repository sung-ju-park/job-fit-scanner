"""LLM 백엔드. 같은 프롬프트로 로컬(Ollama)과 클라우드(Gemini)를 바꿔 쓸 수 있게 한다."""
import json
import os
import re
import time

import requests


class LLMError(Exception):
    pass


class QuotaExceeded(LLMError):
    pass


class LLMTimeout(LLMError):
    pass


def parse_json(text: str) -> dict:
    """모델이 코드블록이나 앞뒤 문장을 붙여도 JSON 부분만 꺼낸다."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"JSON을 찾지 못함: {text[:200]}")
    return json.loads(text[start:end + 1])


OLLAMA_TIMEOUT = 300  # 초. CPU만 쓰는 노트북 기준 공고 하나에 보통 1~3분


def call_ollama(model: str, system: str, user: str) -> str:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        r = requests.post(f"{host}/api/chat", json={
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "format": "json",
            "stream": False,
            # num_predict: 작은 모델이 JSON 모드에서 답을 끝내지 못하고 공백을 계속
            # 이어 쓰는 경우가 있어 출력 길이에 상한을 둔다 (정상 답은 300토큰 이내)
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 600},
        }, timeout=OLLAMA_TIMEOUT)
    except requests.Timeout:
        raise LLMTimeout(f"{OLLAMA_TIMEOUT}초 안에 응답이 없음")
    except requests.RequestException as e:
        raise LLMError(f"Ollama 연결 실패 (Ollama가 켜져 있는지 확인): {e}")
    if r.status_code >= 400:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:300]}")
    return r.json()["message"]["content"]


def call_gemini(model: str, system: str, user: str, max_retries: int = 3) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("GEMINI_API_KEY가 없습니다. .env 파일을 확인하세요.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers={"x-goog-api-key": api_key}, json=body, timeout=120)
        except requests.Timeout:
            raise LLMTimeout("Gemini 응답 시간 초과")
        except requests.RequestException as e:
            raise LLMError(f"Gemini 연결 실패: {e}")
        if r.status_code == 429:
            wait = 30 * attempt
            print(f"  요청 한도 초과(429), {wait}초 대기 후 재시도 ({attempt}/{max_retries})")
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            raise LLMError(f"Gemini {r.status_code}: {r.text[:300]}")
        data = r.json()
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise LLMError(f"빈 응답: {str(data)[:300]}")
        return text
    raise QuotaExceeded("429가 계속 반복됩니다. 일일 무료 한도에 도달했을 수 있습니다.")


BACKENDS = {"ollama": call_ollama, "gemini": call_gemini}
DEFAULT_MODELS = {"ollama": "qwen2.5:3b", "gemini": "gemini-3.5-flash-lite"}
DEFAULT_DELAYS = {"ollama": 0.0, "gemini": 5.0}
