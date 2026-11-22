"""Default locations are relative to this file, never the shell directory."""

from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parent
DATA_DIR = EXPERIMENT_DIR.parent / "Resonance - graduate preparation data"
ARTIFACTS_DIR = EXPERIMENT_DIR / "claim_checker_artifacts"
