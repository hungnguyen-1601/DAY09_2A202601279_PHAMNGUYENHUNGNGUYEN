"""Validate va tao ca hai goi nop theo README va anh Codelab K3.

- output.zip: chi 50 JSON trong output/ (README cohort).
- submission.zip: output/ + architecture.md + trace.jsonl + metadata.json.

Chay: python -B scripts/package_submission.py
"""
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OUTPUT_NAMES = [f"EC_{index:03d}.json" for index in range(1, 51)]
OUTPUT_ARCHIVE_NAMES = [f"output/{name}" for name in EXPECTED_OUTPUT_NAMES]
SUBMISSION_EXTRA_NAMES = ["architecture.md", "trace.jsonl", "metadata.json"]


def _reject_non_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _loads(raw):
    return json.loads(raw, parse_constant=_reject_non_json_constant)


def _run_validator(script_name):
    subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / script_name)],
        cwd=ROOT,
        check=True,
    )


def _validate_sources():
    output_entries = sorted(path.name for path in (ROOT / "output").iterdir())
    if output_entries != EXPECTED_OUTPUT_NAMES:
        raise ValueError("output/ must contain exactly EC_001.json through EC_050.json")
    for name in EXPECTED_OUTPUT_NAMES:
        with (ROOT / "output" / name).open(encoding="utf-8") as handle:
            value = json.load(handle, parse_constant=_reject_non_json_constant)
        if not isinstance(value, dict) or value.get("case_id") != Path(name).stem:
            raise ValueError(f"output/{name} has an invalid case_id")

    required = [
        ROOT / "architecture.md",
        ROOT / "individual_01279_PhamNguyenHungNguyen.md",
        ROOT / "logging" / "trace.jsonl",
        ROOT / "logging" / "metadata.json",
    ]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)

    metadata = _loads((ROOT / "logging" / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("cohort") != "K3":
        raise ValueError("metadata.cohort must be K3")
    if metadata.get("policy_version") != "EC_POLICY_V1":
        raise ValueError("metadata.policy_version must be EC_POLICY_V1")
    if metadata.get("runtime", {}).get("cases") != 50:
        raise ValueError("metadata.runtime.cases must be 50")
    required_metadata = {
        "repo_starter",
        "repository_url",
        "model",
        "model_full_name",
        "parameter_size",
        "provider",
        "framework",
        "tools",
        "runtime",
        "trace_schema_version",
    }
    missing_metadata = sorted(required_metadata - set(metadata))
    if missing_metadata:
        raise ValueError(f"metadata is missing fields: {missing_metadata}")
    if metadata.get("model") != "llama3.2:3b":
        raise ValueError("metadata.model must be llama3.2:3b")
    if not isinstance(metadata.get("tools"), list) or not metadata["tools"]:
        raise ValueError("metadata.tools must be a non-empty list")
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*B\s*", metadata.get("parameter_size", ""))
    if not match or float(match.group(1)) > 10:
        raise ValueError("metadata.parameter_size must be at most 10B")


def _write_zip(target, members):
    temporary = target.with_name(target.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for source, archive_name in members:
                archive.write(source, archive_name)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _verify_zip(target, expected_names):
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        if names != expected_names:
            raise ValueError(f"{target.name} member list is incorrect: {names}")
        for name in names:
            if name.endswith(".json") or name.endswith(".jsonl"):
                raw = archive.read(name).decode("utf-8")
                if name.endswith(".jsonl"):
                    for line in raw.splitlines():
                        _loads(line)
                else:
                    _loads(raw)


def main():
    _run_validator("verify_outputs.py")
    _run_validator("verify_trace.py")
    _validate_sources()

    output_members = [
        (ROOT / "output" / name, f"output/{name}")
        for name in EXPECTED_OUTPUT_NAMES
    ]
    submission_members = output_members + [
        (ROOT / "architecture.md", "architecture.md"),
        (ROOT / "logging" / "trace.jsonl", "trace.jsonl"),
        (ROOT / "logging" / "metadata.json", "metadata.json"),
    ]
    _write_zip(ROOT / "output.zip", output_members)
    _write_zip(ROOT / "submission.zip", submission_members)
    _verify_zip(ROOT / "output.zip", OUTPUT_ARCHIVE_NAMES)
    _verify_zip(
        ROOT / "submission.zip", OUTPUT_ARCHIVE_NAMES + SUBMISSION_EXTRA_NAMES
    )
    print("Created output.zip: 50 output JSON files only")
    print("Created submission.zip: 50 outputs + architecture/trace/metadata")
    return 0


if __name__ == "__main__":
    sys.exit(main())
