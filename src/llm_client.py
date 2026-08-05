"""Client goi LLM local qua Ollama, ep output JSON va log vao trace."""
import json
import time

import requests

from . import config


def _reject_non_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def chat_json(
    tracer,
    case_id,
    agent,
    system,
    payload,
    max_tokens=160,
    retries=2,
    response_validator=None,
):
    """Goi model va parse JSON tra ve. Tra ve None neu that bai sau retries."""
    user_msg = json.dumps(payload, ensure_ascii=False)
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            resp = requests.post(
                f"{config.OLLAMA_URL}/api/chat",
                json={
                    "model": config.MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0, "num_predict": max_tokens},
                },
                timeout=300,
            )
            resp.raise_for_status()
            body = resp.json()
            content = body.get("message", {}).get("content", "")
            latency_ms = int((time.time() - t0) * 1000)
            parsed = json.loads(content, parse_constant=_reject_non_json_constant)
            if response_validator is not None:
                validation_error = response_validator(parsed)
                if validation_error:
                    raise ValueError(validation_error)
            tracer.log(
                case_id,
                agent,
                "llm_call",
                model=config.MODEL_NAME,
                attempt=attempt,
                input=payload,
                output_raw=content,
                latency_ms=latency_ms,
                prompt_tokens=body.get("prompt_eval_count"),
                output_tokens=body.get("eval_count"),
            )
            return parsed
        except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as exc:
            tracer.log(
                case_id,
                agent,
                "llm_error",
                model=config.MODEL_NAME,
                attempt=attempt,
                error=str(exc)[:300],
                latency_ms=int((time.time() - t0) * 1000),
            )
    return None
