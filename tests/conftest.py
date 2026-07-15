"""pytest conftest — adds the project root to sys.path so tests can import app modules."""
import sys
from pathlib import Path

# Make `from utils import ...`, `import api`, etc. work from any test in tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
