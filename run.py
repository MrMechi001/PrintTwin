"""Start the PrintTwin bridge (works from any working directory, also with python -I)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from printtwin.__main__ import main  # noqa: E402

main()
