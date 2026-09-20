"""Makes the flat modules in Prototype3/ (algorithm.py, algorithm_weighted.py,
helpers.py, main.py) importable from tests/ regardless of which directory
pytest is invoked from."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
