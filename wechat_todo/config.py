"""配置装载：TOML 提供非敏感项，.env / 环境变量提供密钥与端点覆盖。

优先级：环境变量 > .env > config.toml。
密钥与 base_url 一律可配，任何 OpenAI 兼容端点都能接。
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# 用户数据（.env / config.toml / decrypted / data / out）跟 exe 或仓库根目录走
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
# 只读资源（前端 dist、上游采集脚本）：PyInstaller 解包目录或仓库根目录
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))


def _load_dotenv(path: Path) -> None:
    """把 .env 灌进 os.environ（不覆盖已存在的环境变量）。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class ConfigError(RuntimeError):
    """配置缺失或自相矛盾。宁可报错退出，也不猜。"""


@dataclass
class Config:
    # --- LLM（OpenAI 兼容）---
    base_url: str
    api_key: str
    model: str
    response_format: str = "json_object"  # json_object | json_schema
    temperature: float = 0.0
    timeout: float = 180.0
    max_chars_per_request: int = 12000

    # --- 采集 ---
    db_dir: Path | None = None
    work_dir: Path = ROOT / "decrypted"
    keys_file: Path = ROOT / "keys.json"
    tool_path: Path = ROOT / "tools" / "wcdb-key-tool-main" / "wcdb_key_tool_windows.py"

    # --- 过滤 ---
    whitelist: list[str] = field(default_factory=list)
    text_types: list[int] = field(default_factory=lambda: [1])
    window_hours: int = 48
    max_messages_per_session: int = 5000

    # --- 产出 ---
    state_db: Path = ROOT / "data" / "state.db"
    out_dir: Path = ROOT / "out"

    # --- 面板 ---
    host: str = "127.0.0.1"
    port: int = 8787

    def require_llm(self) -> None:
        missing = [n for n, v in (("base_url", self.base_url), ("api_key", self.api_key),
                                  ("model", self.model)) if not v]
        if missing:
            raise ConfigError(
                "缺少 LLM 配置：" + ", ".join(missing) +
                "。在 .env 里填 WECHAT_TODO_BASE_URL / WECHAT_TODO_API_KEY / WECHAT_TODO_MODEL。")

    def require_whitelist(self) -> None:
        if not self.whitelist:
            raise ConfigError(
                "白名单为空。在 config.toml 的 [filter].whitelist 里填会话关键词"
                "（wxid / xxxx@chatroom / 备注名 / 群名片段均可），先跑 "
                "`python scripts/run.py --list-sessions` 看全部会话。")


def _auto_db_dir() -> Path | None:
    """微信 4.x 默认把库放在 Documents\\xwechat_files\\<wxid>\\db_storage。"""
    docs = Path(os.path.expanduser("~")) / "Documents" / "xwechat_files"
    if not docs.is_dir():
        return None
    for acc in sorted(docs.glob("wxid_*")):
        candidate = acc / "db_storage"
        if candidate.is_dir():
            return candidate
    return None


def load(config_path: Path | None = None) -> Config:
    _load_dotenv(ROOT / ".env")

    path = config_path or (ROOT / "config.toml")
    raw: dict = {}
    if path.is_file():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

    llm = raw.get("llm", {})
    collect = raw.get("collect", {})
    filt = raw.get("filter", {})
    out = raw.get("output", {})
    srv = raw.get("server", {})

    db_dir = collect.get("db_dir") or _auto_db_dir()

    cfg = Config(
        base_url=os.environ.get("WECHAT_TODO_BASE_URL") or llm.get("base_url", ""),
        api_key=(os.environ.get("WECHAT_TODO_API_KEY")
                 or os.environ.get("DEEPSEEK_API_KEY") or llm.get("api_key", "")),
        model=os.environ.get("WECHAT_TODO_MODEL") or llm.get("model", ""),
        response_format=llm.get("response_format", "json_object"),
        temperature=float(llm.get("temperature", 0.0)),
        timeout=float(llm.get("timeout", 180.0)),
        max_chars_per_request=int(llm.get("max_chars_per_request", 12000)),
        db_dir=Path(db_dir) if db_dir else None,
        work_dir=Path(collect.get("work_dir") or ROOT / "decrypted"),
        keys_file=Path(collect.get("keys_file") or ROOT / "keys.json"),
        tool_path=Path(collect.get("tool_path")
                       or RESOURCE_ROOT / "tools" / "wcdb-key-tool-main"
                       / "wcdb_key_tool_windows.py"),
        whitelist=list(filt.get("whitelist", [])),
        text_types=[int(x) for x in filt.get("text_types", [1])],
        window_hours=int(filt.get("window_hours", 48)),
        max_messages_per_session=int(filt.get("max_messages_per_session", 5000)),
        state_db=Path(out.get("state_db") or ROOT / "data" / "state.db"),
        out_dir=Path(out.get("out_dir") or ROOT / "out"),
        host=srv.get("host", "127.0.0.1"),
        port=int(srv.get("port", 8787)),
    )
    return cfg
