"""Arranque em modo de desenvolvimento (sem instalar o wheel).

Equivalente a `bgo-scheduler` depois de `pip install .`:
    python systray_icon.py [--headless] [--config INI] [--apps-root PASTA]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bgo_scheduler.cli import main

if __name__ == "__main__":
    main()
