import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 测试隔离（v4.3 T_META / v4.4.1 名单两次事故根因同源）：
# data_dir() 便携优先=CWD/data（读的是真实用户配置）。让每个 pytest 进程
# 在一次性临时目录里跑，CWD/data 落临时区，测试与本机数据互不污染。
_TMP = Path(tempfile.mkdtemp(prefix="sp_test_"))
os.chdir(_TMP)
(_TMP / "data").mkdir(exist_ok=True)
