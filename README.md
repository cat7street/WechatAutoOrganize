# WechatAutoOrganize

把微信聊天记录自动导出，交给 LLM 抽成待办事项，落到 Markdown 日报 + 本地面板。

> 数据全程留在本机：解密、抽取、状态库、面板都跑在 `127.0.0.1`。唯一出网的是发给你自己配置的 LLM 端点。

## 实测环境

| 项 | 值 |
|---|---|
| 微信 | **4.2.0.11**（Windows，`Weixin.exe`） |
| 解密 | **20/20 个库全部命中**，`integrity_check = ok`，0.2 GB |
| 权限 | 读进程内存提 key **不需要管理员**（同用户进程） |
| 真实数据 | 183 个会话 / 94 张 `Msg_*` 表 / 80,438 条消息 |

## 架构

```
微信进程内存 ──┐
              │  上游 wcdb-key-tool（不重写，进程内调用）
              ▼
        decrypted/*.db  (SQLCipher 4 → 明文 SQLite，只读 immutable 打开)
              ▼
        scan.py    白名单匹配 + 水位线增量 + 剥群聊前缀 + zstd 解压 + 发送人映射
              ▼
        extract.py 按字符预算分批 → LLM 结构化输出（JSON Schema）
              ▼
        store.py   三层去重（hash）+ 水位线 + token 记账
              ▼
   out/YYYY-MM-DD.md   +   FastAPI 面板（127.0.0.1:8787）
```

## 技术栈

| 层 | 选型 |
|---|---|
| 采集 | [`TANGandXUE/wcdb-key-tool`](https://github.com/TANGandXUE/wcdb-key-tool)（Windows `Config.Cipher` 运行时扫描） |
| 后端 | Python 3.12 + FastAPI + SQLite（stdlib `sqlite3`，无 ORM、无容器） |
| 模型 | **任何 OpenAI 兼容端点**，`base_url` / `api_key` / `model` 全部可配 |
| 前端 | React 19 + TypeScript + Vite + Tailwind 4 + shadcn 风格本地组件 + TanStack Query + ECharts |
| 类型 | `openapi-typescript` 从 `/openapi.json` 生成，前后端字段不漂移 |
| 打包 | PyInstaller 单文件 exe（前端 dist 内嵌） |

## 快速开始

### 1. 前置

- Windows + 微信 4.x **已登录并保持运行**（提 key 要读活进程内存）
- Python 3.12、Node 20+（`pnpm`）
- 取上游采集脚本（不随本仓库分发）：

```powershell
# 拉一份到 tools/（已被 .gitignore 挡住，不会进仓库）
curl.exe -L -o tools.tgz https://codeload.github.com/TANGandXUE/wcdb-key-tool/tar.gz/refs/heads/main
tar -xzf tools.tgz -C tools
Rename-Item tools\wcdb-key-tool-main <...>   # 确保路径为 tools\wcdb-key-tool-main\
```

### 2. 后端

```powershell
python -m venv tools\venv
tools\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.toml config.toml
Copy-Item .env.example .env      # 然后填 base_url / api_key / model
```

### 3. 前端

```powershell
cd frontend
pnpm install
pnpm gen:api        # 后端起着时刷新 TS 类型
pnpm build
```

### 4. 跑

```powershell
# 列出 183 个会话，勾白名单用
tools\venv\Scripts\python.exe scripts\run.py --list-sessions

# 干跑：只组装请求包到 data/prompt_dump.json，不花钱
tools\venv\Scripts\python.exe scripts\run.py --skip-decrypt --dry-run

# 正式跑一次增量
tools\venv\Scripts\python.exe scripts\run.py

# 开面板（单进程，API + 前端一起）
tools\venv\Scripts\python.exe scripts\serve.py --open
```

### 5. 打成 exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 产物 dist\WechatAutoOrganize.exe（约 22 MB），双击即开面板
```

## 配置

非敏感项在 `config.toml`，密钥在 `.env`（环境变量优先级最高）。

```toml
[llm]
base_url = "https://api.deepseek.com/v1"   # 换成任何 OpenAI 兼容端点
model = "deepseek-chat"
response_format = "json_object"            # 端点支持结构化输出可改 json_schema

[filter]
whitelist = ["示例群名", "wxid_xxxx", "12345678@chatroom"]  # username/备注名/群 ID 片段
window_hours = 48
```

```ini
# .env
WECHAT_TODO_BASE_URL=https://api.deepseek.com/v1
WECHAT_TODO_API_KEY=sk-xxx
WECHAT_TODO_MODEL=deepseek-chat
```

面板「设置」页也能改：**api_key 只写不读**，保存后界面只显示 `sk-x****abcd`，明文只落在 `.env`。

## 去重与幂等

| 层 | 机制 |
|---|---|
| 消息级 | `cursor` 表按会话记 `sort_seq`（毫秒）水位线，只取新消息 |
| 任务级 | `sha256(会话 + 归一化标题 + 截止 + 责任人)` 唯一索引，重复直接丢弃 |
| 引用级 | LLM 给出的 `msg_ids` 必须落在本批消息集合内，否则整条丢弃（防幻觉） |

## 落地过程中实测出来的坑（都写进代码了）

1. **解密目录必须 `mode=ro&immutable=1` 打开**。以读写模式碰过一次，SQLite 留下零长 `-wal`+`-shm`，之后所有查询都看到**空 schema（0 张表）**。`decrypt.py` 每次解密后都会清理 sidecar。
2. **`sort_seq` 是毫秒，`create_time` 是秒**。拿秒当水位线比毫秒，时间窗口会完全失效（早期真抽到了 48 小时之前的消息）。
3. **群聊文本自带 `发送人:\n` 前缀**，喂模型前必须剥掉。
4. **非文本消息体是 zstd**（magic `28 b5 2f fd`），文本类型（`local_type=1`）才是明文。
5. **表名规则**：`Msg_<md5(username)>`，用 `session.db.SessionTable.username` 算 md5 匹配（183 会话命中 94 张表，其余确实无消息）。
6. **`.db-wal` 未合并**：解密只处理主库，未 checkpoint 的最新消息会缺，增量刷新需要 WAL patch（见上游 `wechat-local-reader`）。
7. **不要用 PowerShell 5.1 的 `Get-Content -Raw` 改带中文的文件**：会按 GBK 解码再以 UTF-8 写回，把 `.gitignore` 里的规则粘到注释行上——`.env` 就这样差点被提交。

## 目录

```
wechat_todo/
  config.py     TOML + .env 装载，base_url/api_key 可配；缺配置直接报错退出
  decrypt.py    进程内 runpy 调上游采集脚本（冻结 exe 后 sys.executable 会套娃）
  scan.py       只读读库：会话列表、白名单、增量消息、归一化
  extract.py    提示词与分批
  llm.py        OpenAI 兼容客户端 + JSON 结构化输出
  store.py      水位线 / 待办 / 运行记录 / token 记账
  render.py     Markdown 日报
  pipeline.py   iter_run() 一个生成器，CLI 与 SSE 共用
  server.py     FastAPI + SSE + 静态托管
frontend/       React 面板（5 页：概览 / 白名单 / 待办 / 运行 / 设置）
scripts/        run.py（CLI）、serve.py（单进程）、build_exe.ps1
```

## 已知限制

- 只抽取文本消息（`local_type=1`）。图片/语音/文件需要用视觉或 ASR，另开一期。
- 群公告式刷屏需要靠白名单挡，不要在提示词层兜底。
- 前端单 chunk 约 1.5 MB（ECharts 在概览页），后续可按路由懒加载拆包。
- 抽取质量取决于模型：结构化输出 + 强制 `msg_ids` 回指原文是目前最有效的降幻觉手段。

## 声明

- 只处理**你自己账号、你自己设备**上的数据；不要在别人的机器上跑。
- 解密能力来自上游开源项目，本仓库不包含其代码。上游 `chatlog`、`PyWxDump` 已于 2025-10 被发函删库，Windows 4.x 路线请自行评估合规风险。
- 仓库内不含任何密钥、解密产物或聊天明文；`.env`、`decrypted/`、`keys.json`、`tools/` 均在 `.gitignore` 中。
