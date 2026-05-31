"""pytest 共通設定 — agent ルートを import パスに追加する。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
