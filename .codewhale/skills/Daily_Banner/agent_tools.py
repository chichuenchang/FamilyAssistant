"""Daily_Banner 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。无工具，只挂 FAST_TICKS。"""

from __future__ import annotations

from banner import tick

ORDER = 100

FAST_TICKS = [tick]
