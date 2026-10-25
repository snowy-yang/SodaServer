"""测试隔离：所有用例使用独立 DATA_DIR，绝不读写真实的 data/auth.json。

必须在任何 app 模块导入前设置；config.data_dir() 是运行时读取环境变量的。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="soda-server-tests-")
os.environ.pop("AUTH_KEY", None)
os.environ.pop("RATE_LIMIT", None)
os.environ.pop("QS_SIGN_SERVER", None)
