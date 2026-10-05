"""
Small helper meant to be shared by the training scripts so metrics.json
accumulates a run history (see ml/README.md) instead of each script
overwriting whatever the other one wrote.

STATUS: not currently called by anything. scripts/train_models.py -- the
trainer actually wired into the live API -- writes ml/models/metrics.json
as a flat overwrite (a single {"price_forecast": {...}} dict per run, see
scripts/train_models.py's save_artifacts()), not the {"runs": [...]}
accumulating structure append_run() below produces. Wire this in (replace
that script's json.dump(...) call with append_run(...)) if you want run
history instead of only ever seeing the latest metrics.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

METRICS_PATH = Path(__file__).resolve().parents[1] / "models" / "metrics.json"


def append_run(model_name: str, metrics: dict, note: str = "") -> None:
    if METRICS_PATH.exists():
        payload = json.loads(METRICS_PATH.read_text())
    else:
        payload = {"runs": []}

    payload["runs"].append(
        {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "model": model_name,
            "note": note,
            "metrics": metrics,
        }
    )
    METRICS_PATH.write_text(json.dumps(payload, indent=2))
