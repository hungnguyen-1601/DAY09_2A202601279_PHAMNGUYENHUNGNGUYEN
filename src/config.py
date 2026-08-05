"""Cau hinh chung cho he thong multi-agent EC dispute resolution."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logging"
TRACE_PATH = LOG_DIR / "trace.jsonl"
METADATA_PATH = LOG_DIR / "metadata.json"

# Model name khai bao trong code theo yeu cau README muc 9.4.
# Meta Llama 3.2 3B Instruct - 3B parameters (<= gioi han 10B), chay local qua Ollama.
MODEL_NAME = "llama3.2:3b"
MODEL_PARAMETER_SIZE = "3B"
MODEL_PROVIDER = "ollama (local)"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

POLICY_VERSION = "EC_POLICY_V1"
PAYMENT_TOLERANCE_BRL = 0.10

# Gioi han schema output (README muc 6)
MAX_ENTITY_IDS = 5
MAX_EVIDENCE = 10
MAX_ROOT_CAUSES = 3
MAX_PARTIES = 3
MAX_ACTIONS = 5
