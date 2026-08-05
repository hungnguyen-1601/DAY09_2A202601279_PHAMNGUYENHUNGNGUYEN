"""Ghi trace JSONL theo co che atomic va co contract handoff bat buoc."""
import json
import time
from pathlib import Path

from .handoffs import validate_handoff


class Tracer:
    def __init__(self, path):
        self.path = Path(path)
        self.temp_path = self.path.with_name(self.path.name + ".tmp")
        # Giu nguyen trace thanh cong truoc do trong khi batch moi dang chay.
        self._fh = open(self.temp_path, "w", encoding="utf-8")
        self._closed = False

    def log(self, case_id: str, agent: str, event: str, **payload):
        if self._closed:
            raise RuntimeError("Cannot write to a closed trace")
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "case_id": case_id,
            "agent": agent,
            "event": event,
        }
        record.update(payload)
        self._fh.write(
            json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        )
        self._fh.flush()

    def handoff(self, case_id: str, to: str, handoff: dict):
        """Validate va ghi mot handoff day du; cam event handoff rong."""
        errors = validate_handoff(handoff, expected_recipient=to)
        if handoff.get("ticket_id") != case_id:
            errors.append("ticket_id does not match trace case_id")
        if errors:
            raise ValueError("Invalid handoff trace: " + "; ".join(errors))
        self.log(case_id, "coordinator", "handoff", to=to, **handoff)

    def _close_file(self):
        if not self._closed:
            self._fh.flush()
            self._fh.close()
            self._closed = True

    def commit(self):
        """Cong bo trace moi chi sau khi batch hoan tat."""
        self._close_file()
        self.temp_path.replace(self.path)

    def abort(self):
        """Bo trace dang do va giu nguyen trace thanh cong truoc do."""
        self._close_file()
        self.temp_path.unlink(missing_ok=True)

    def close(self):
        """Backward-compatible alias: mot close binh thuong la commit."""
        self.commit()
