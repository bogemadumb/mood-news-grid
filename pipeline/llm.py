"""Доступ к языковой модели.

Один интерфейс, за ним несколько провайдеров:
  claude_cli — подписка Claude через её командную строку (текущий основной путь);
  openai     — любой OpenAI-совместимый endpoint: z.ai, LM Studio, Ollama;
  echo       — детерминированная заглушка, чтобы проект поднимался без ключей и без сети.

Провайдер выбирается переменной LLM_PROVIDER. Смена провайдера не требует правок
в остальном коде — это и ответ на вопрос «а если у проверяющего нет вашей подписки».

Каждый вызов пишется в таблицу llm_calls. Она работает сразу как кэш (одинаковый промпт
не оплачивается дважды), как источник кассет для воспроизведения тестов и как доказательство,
что текст породила модель, а не рука.
"""
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.db import session


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    http_status: int = 200
    error: str | None = None
    call_id: int | None = None
    from_cache: bool = False


def request_hash(provider: str, model: str, temperature: float, prompt: str) -> str:
    raw = f"{provider}|{model}|{temperature}|{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def wrap_untrusted(text: str) -> tuple[str, str]:
    """Обернуть чужой текст в блок с одноразовой меткой.

    Текст новости приходит из интернета, то есть это недоверенный ввод. Если внутри статьи
    окажется строка «игнорируй предыдущие инструкции», модель не должна принять её за команду.
    Метка случайная на каждый вызов, поэтому её нельзя подделать заранее в тексте статьи.
    """
    nonce = uuid.uuid4().hex[:8]
    return nonce, f"<<<ARTICLE {nonce}>>>\n{text}\n<<<END {nonce}>>>"


class EchoProvider:
    """Заглушка: переставляет предложения и добавляет метку тона.

    Нужна не для красоты — с ней `make dev` поднимается на чистой машине без ключей,
    а тесты не ходят в сеть.
    """

    name = "echo"

    def complete(self, prompt: str, model: str, temperature: float, max_tokens: int) -> LLMResult:
        match = re.search(r"<<<ARTICLE \w+>>>\n(.*?)\n<<<END", prompt, re.S)
        body = (match.group(1) if match else prompt)[:1200]
        return LLMResult(
            text=json.dumps({"title": "Заглушка", "body": body}, ensure_ascii=False),
            provider=self.name, model="echo", latency_ms=0,
        )


class ClaudeCliProvider:
    """Подписка Claude через её командную строку.

    Токен не передаётся приложению: на сервере команда выполняется от пользователя,
    у которого он уже есть. Публичный веб-процесс живёт под другим пользователем
    и до токена не дотягивается.
    """

    name = "claude_cli"

    def __init__(self):
        self.binary = os.getenv("CLAUDE_BIN", "claude")
        self.model = os.getenv("CLAUDE_MODEL", "sonnet")

    def complete(self, prompt: str, model: str, temperature: float, max_tokens: int) -> LLMResult:
        t0 = time.time()
        cmd = [self.binary, "-p", prompt, "--model", model or self.model,
               "--dangerously-skip-permissions"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return LLMResult("", self.name, model, int((time.time() - t0) * 1000),
                             http_status=504, error="таймаут 180 с")
        ms = int((time.time() - t0) * 1000)
        if proc.returncode != 0:
            return LLMResult("", self.name, model, ms, http_status=500,
                             error=(proc.stderr or "")[:500])
        return LLMResult(proc.stdout.strip(), self.name, model or self.model, ms)


class OpenAICompatibleProvider:
    """Любой endpoint с интерфейсом OpenAI: z.ai, LM Studio, Ollama."""

    name = "openai"

    def __init__(self):
        self.base = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
        self.key = os.getenv("LLM_API_KEY", "not-needed")

    def complete(self, prompt: str, model: str, temperature: float, max_tokens: int) -> LLMResult:
        t0 = time.time()
        try:
            resp = httpx.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                json={"model": model, "temperature": temperature, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=180,
            )
        except httpx.HTTPError as exc:
            return LLMResult("", self.name, model, int((time.time() - t0) * 1000),
                             http_status=0, error=str(exc)[:300])
        ms = int((time.time() - t0) * 1000)
        if resp.status_code != 200:
            return LLMResult("", self.name, model, ms, http_status=resp.status_code,
                             error=resp.text[:300])
        data = resp.json()
        usage = data.get("usage") or {}
        return LLMResult(
            data["choices"][0]["message"]["content"], self.name, model, ms,
            tokens_in=usage.get("prompt_tokens", 0), tokens_out=usage.get("completion_tokens", 0),
        )


PROVIDERS = {
    "claude_cli": ClaudeCliProvider,
    "openai": OpenAICompatibleProvider,
    "echo": EchoProvider,
}


def get_provider(name: str | None = None):
    key = (name or os.getenv("LLM_PROVIDER", "echo")).strip()
    if key not in PROVIDERS:
        raise ValueError(f"неизвестный провайдер: {key}. Доступны: {', '.join(PROVIDERS)}")
    return PROVIDERS[key]()


def complete(prompt: str, *, purpose: str, model: str = "", temperature: float = 0.8,
             max_tokens: int = 1600, provider: str | None = None,
             use_cache: bool = True) -> LLMResult:
    """Вызвать модель с кэшированием и записью в журнал."""
    prov = get_provider(provider)
    model = model or getattr(prov, "model", "default")
    rhash = request_hash(prov.name, model, temperature, prompt)

    if use_cache:
        with session() as conn:
            row = conn.execute(
                "SELECT id, response_text, latency_ms, tokens_in, tokens_out"
                " FROM llm_calls WHERE request_hash=? AND error IS NULL", (rhash,)
            ).fetchone()
            if row and row["response_text"]:
                return LLMResult(row["response_text"], prov.name, model, row["latency_ms"],
                                 row["tokens_in"] or 0, row["tokens_out"] or 0,
                                 call_id=row["id"], from_cache=True)

    result = prov.complete(prompt, model, temperature, max_tokens)

    with session() as conn:
        cursor = conn.execute(
            """INSERT INTO llm_calls (request_hash, purpose, provider, model, request_json,
                                      prompt_sha256, response_text, tokens_in, tokens_out,
                                      latency_ms, http_status, error, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(request_hash) DO UPDATE SET
                 response_text=excluded.response_text, error=excluded.error,
                 latency_ms=excluded.latency_ms""",
            (rhash, purpose, prov.name, model, prompt[:2000],
             hashlib.sha256(prompt.encode()).hexdigest(), result.text,
             result.tokens_in, result.tokens_out, result.latency_ms,
             result.http_status, result.error, datetime.now(timezone.utc).isoformat()),
        )
        result.call_id = cursor.lastrowid or conn.execute(
            "SELECT id FROM llm_calls WHERE request_hash=?", (rhash,)).fetchone()["id"]

    return result


def parse_json_answer(raw: str) -> dict | None:
    """Достать JSON из ответа модели.

    Модели регулярно оборачивают ответ в ```json и добавляют вежливое вступление,
    несмотря на прямой запрет в промпте. Это не повод считать ответ испорченным.
    """
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "body" not in data:
        return None
    return {"title": str(data.get("title", "")).strip(), "body": str(data["body"]).strip()}
