"""切分 + 向量化 + 入库的 CLI。

真正的构建逻辑在 `app/kb/build.py`，审核通过后的同步也复用同一函数。
本脚本只负责命令行入口与输出。

流程：静态语料 + 已审核 FAQ → 算 kb_version → Ollama 向量化
→ upsert 到 Chroma → 删除多余 id → 更新 FAQ/canonical 状态。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/build_kb.py` 直接跑

from app.config import get_settings
from app.kb.build import build_kb


async def main() -> int:
    try:
        result = await build_kb()
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一转成可读错误
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    print(f"集合 {get_settings().chroma_collection} 已更新")
    print(
        f"chunks={result['chunks']}  faq={result['faq']}  "
        f"deleted={result['deleted']}  kb_version={result['version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
