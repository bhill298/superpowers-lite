"""Load the standalone installer without requiring an importable filename."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'superpowers_lite', Path(__file__).resolve().parents[1] / 'superpowers-lite.py')
lite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lite)
