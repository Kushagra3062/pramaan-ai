"""One-call Meta Model API connectivity test. Run from backend/ with .env at repo root."""
import sys
from pathlib import Path

# Allow running `python scripts/meta_smoke_test.py` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging_config import configure_logging  # noqa: E402
from app.services.meta_service import smoke_test_model  # noqa: E402

configure_logging()

if __name__ == "__main__":
    result = smoke_test_model()
    print(f"Status: {result['status']}")
    print(f"Model: {result['model']}")
    print(f"Output: {result['output']}")
    print(f"Latency: {result['duration_ms']} ms")
    print(f"Usage: {result['usage']}")
    if result["status"] != "ok":
        raise SystemExit(1)
