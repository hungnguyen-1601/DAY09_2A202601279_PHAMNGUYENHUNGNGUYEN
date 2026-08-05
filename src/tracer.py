"""Ghi trace.jsonl: moi dong la mot event (handoff, llm_call, verification...)."""
import json
import time


class Tracer:
    def __init__(self, path):
        self.path = path
        # README: khong append, chi giu luot chay moi nhat -> truncate khi mo.
        self._fh = open(path, "w", encoding="utf-8")

    def log(self, case_id: str, agent: str, event: str, **payload):
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "case_id": case_id,
            "agent": agent,
            "event": event,
        }
        record.update(payload)
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()
