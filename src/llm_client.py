"""Client goi LLM local qua Ollama, ep output JSON va log vao trace."""
import json
import time

import requests

from . import config


def chat_json(tracer, case_id, agent, system, payload, max_tokens=160, retries=2):
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
            return json.loads(content)
        except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
            tracer.log(
                case_id,
                agent,
                "llm_error",
                model=config.MODEL_NAME,
                attempt=attempt,
                error=str(exc)[:300],
            )
    return None
