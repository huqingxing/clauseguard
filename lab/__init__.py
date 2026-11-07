"""检索实验台包。

为什么在 __init__ 里修 sys.path：lab/ 位于仓库根，而它复用的 chunker / bm25 /
fusion / service 在 python-agent/ 下，两套代码分属不同 Python 项目（uv 的 venv
只认 python-agent 为项目根）。把路径收敛到一处，run.py / report.py / verify.py
只需 `import lab.*` 即可，避免每个入口各写一遍、某天漏掉一个而只在 CI 里炸。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PYAGENT = _REPO / "python-agent"

for _path in (str(_REPO), str(_PYAGENT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
