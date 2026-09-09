"""开发运行入口：python main.py [--smoke]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from stockpilot.main import app_main  # noqa: E402

if __name__ == "__main__":
    app_main()
