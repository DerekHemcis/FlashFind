"""FlashFind 启动入口：python app.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flashfind.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
