# WechatAutoOrganize

把微信聊天记录自动导出，交给 LLM 抽成待办事项，落到 Markdown 日报 + 本地面板。

> 数据全程留在本机：解密、抽取、状态库、面板都跑在 `127.0.0.1`。唯一出网的是发给你自己配置的 LLM 端点。

## 媒体消息（图 / 文件 / 语音 / 引用 / 转发 / 转账）

微信 4.x 把 appmsg 子类型**打包进 `local_type` 高 32 位**：`base = local_type & 0xFFFFFFFF`，`subtype = local_type >> 32`。按下表处理：

| 消息 | base/subtype | 处理方式 | 实测状态 |
|---|---|---|---|
| 文本 | 1 | 直接读 | ✅ |
| 图片 | 3 | 解 `.dat` → JPEG → 作为图像喂多模态模型 | ✅ 可解，见下方限制 |
| 语音 | 34 | 只给时长；本机无音频文件，不做转写 | ⚠️ 元信息 |
| 视频 | 43 | 时长元信息 | ⚠️ 元信息 |
| 文件 | 49/6 | 按 md5 定位 → 本地抽正文（xlsx/docx/pdf/txt/csv/md） | ✅ |
| 引用 | 49/57 | 把被引用原文与当前回复拼成一条 | ✅ |
| 合并转发 | 49/19 | 解析 `recorditem` 里的整段聊天 | ✅ |
| 链接/小程序/视频号 | 49/5,33,36,51,62 | 标题+描述+URL | ✅ |
| 转账/红包 | 49/2000,2001 | 金额与说明 | ✅ |
| 拍一拍 | 49/* | 单独归类，不污染链接计数 | ✅ |
| 表情 | 47 | 丢弃（纯噪声） | — |
| 系统/撤回 | 10000 | 撤回内容作为上下文 | ✅ |

实测一轮（2 个白名单会话）：4,302 条消息里带出 **121 张图片、171 条引用、12 条语音、9 条视频、2 条合并转发、13 条钱款**，切成 15 批请求。

### 媒体层的三个真实限制

1. **原图是微信自有 WXGF 容器（HEVC）**，不是 JPEG。`<md5>.dat` 解出来头是 `wxgf`，PIL 打不开；能直接用的 `<md5>_t.dat` 缩略图（120–700 px）。代码按 `_t → 原图 → _h` 逐变体尝试，以 PIL 能否打开为唯一判定，全失败就返回空并写明原因——**不会把原始 .dat 当图片交上去**。
2. **老图片的 .dat 已被微信清缓存**：抽样 80 条图片消息，33 条能解，47 条本机没有文件（不是代码问题，是数据没了）。
3. **语音没有本地音频**：63 条语音消息，全盘 0 个 `.silk/.amr/.mp3`。要转写必须先让微信把语音下载下来，或从 CDN 拉，另开一期。

图片 key 是另一把密钥（V2 格式 = AES-128-ECB + XOR），不在数据库密钥里，需从进程内存另取；本项目直接复用上游 `find_image_key.py`，密钥落在 `tools/wechat-decrypt/config.json` 并缓存到 `data/image_keys.json`。


## 查看原文 / 语义合并 / 反复提及提级

| 能力 | 入口 | 说明 |
|---|---|---|
| 看某条待办的出处 | 待办页「看上下文」 | 拉这条待办引用消息的前后 6 条，命中的原文高亮标「待办依据」；支持深链 `/tasks?task=<id>` |
| 翻整个会话 | 会话白名单页「查看」 | 从最新往前翻，每次 60 条，可继续加载更早 |
| AI 合并同一件事 | 待办页「AI 合并去重」/ `POST /api/merge` | 模型判断哪些条目在说同一件事，合并后 msg_ids 取并集，被吸收的置 `status=merged` |
| 反复提及提级 | 自动 | `mentions` = 合并后消息条数；≥2 条至少 medium，≥5 条直接 high（模型给的档位再低也会被抬上去） |

优先级由「模型判断 + 提及次数下限」两者取高，所以规则可复算、可解释：

```python
def floor_by_mentions(n):
    return "high" if n >= 5 else "medium" if n >= 2 else "low"
```

实测一轮：79 条待办 → 聚成 6 组、吸收 8 条、提级 10 条；`示例事项` 因被提及 10 次升为 high。


## 取消 / 替代 / 反复：交给模型整体判断

待办的生命周期不是"抽出来就固定"，会被后续对话推翻、替代，也可能被重新要做。

**做法：把整段时间窗（默认 48 小时）的完整对话一次性交给模型，让它自己逐条判断。**
不给"每条待办一个小窗口"——那等于人先替模型筛掉了证据（"换服务器"的结论往往在离待办很远的地方）。

模型输出结构化判定：

```json
{"decisions":[{"task_id":73,"final_state":"pending|cancelled|superseded",
  "replacement":"新方案是什么","evidence_quote":"逐字原文",
  "decided_at_msg_id":6521,"confidence":0.8}]}
```

**规则只验机器能验的东西**（不猜语义）：

| 校验 | 说明 |
|---|---|
| `decided_at_msg_id` 必须在窗口内 | 防编造位点 |
| `evidence_quote` 必须逐字出自窗口 | 防改写原文 |
| 证据最短 4 个字 | 只引"换""行"这种会被驳回 |
| `confidence < 0.6` 只标注不改状态 | 变成「疑似…」，留给人确认 |
| 终态非法直接驳回 | — |

**反复也按终态处理**：`pending ↔ cancelled` 都能来回改，改动留痕在 `state_note`。
判定回 pending 时会清掉旧的取消痕迹，避免误导。

实测（37 条待办 / 4 次调用）：

```
#73 续费 示例项目 服务器
   一轮判 superseded（"已下单新服务器"）
   但后文有 #1001「本月无法完成迁移」#1002「备案仍绑定在原机器上」
   重判 → pending，备注「核对后确认仍要做（换服务器未成，仍需续费）」
#41 跟进 <事项 A>  → cancelled（依据 msg1003「暂时不用处理」）
#39 修复 <事项 B> → 疑似 cancelled（低置信度，只标注）
```

开关：

```toml
[reconcile]
enabled = true
window_hours = 48          # 看多久的对话
max_context_chars = 160000 # 超了从最早截断，保留最新
tasks_per_call = 12        # 每次调用带多少条待办
max_calls = 8
```


## 定时运行（内置，不依赖系统计划任务）

面板「设置 → 定时运行」一开，程序自己按间隔跑：

```
解密 → WAL 合并 → 48h 全量对账 → 合并去重 → 出当天 Markdown
```

**定时一律走「全量对账」（`mode = "full"`）**：把最近 `window_hours` 的**完整对话** +
**当前待办清单**一起交给模型，让它一次给出全部差异：

| 差异 | 说明 |
|---|---|
| `new` | 对话里新出现、清单里没有的待办 |
| `update` | 已有待办改标题 / 截止 / 责任人 / 优先级（“改成下周一”也能改） |
| `cancel` | 明确不做了 |
| `supersede` | 被新方案取代（“换新服务器”、“改成私有”） |
| `restore` | 之前判取消、后来又要做 |

只走增量抽取会失真：模型看不到全局，既发现不了“后来不要了”，也改不了已有字段。

```toml
[pipeline]
mode = "incremental"   # 手动运行默认；定时任务固定用 full

[schedule]
enabled = false
mode = "full"
interval_minutes = 60
skip_decrypt = false   # 定时那轮跳过重新解密（WAL 照样合并，更快）
run_on_start = true
```

实测：

- 定时器按 60 秒间隔触发一轮，`last_result=成功`，单轮 83.7k tokens；关闭后 `next_at=null`
- 一轮全量对账（48h、1784 条消息、3 次调用）：**新增 3、更新 6、被替代 1、恢复 1**，耗时约 7 分钟
- 上一轮还没跑完时，下一轮会**跳过**（不排队堆积），状态里的 `skipped` 会 +1

> 单轮全量对账要几分钟（对话长、模型慢），间隔别设太短；建议 30–60 分钟。
> 想更快可把 `skip_decrypt = true`（WAL 合并仍会补上最新消息）。


## 浏览器配置

启动时自动打开面板，浏览器可配：

```toml
[server]
host = "127.0.0.1"
port = 8787
open_browser = true          # 启动是否自动开浏览器
browser = ""                 # 留空用系统默认；也可指定具体浏览器
# browser = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

命令行覆盖（优先级高于配置）：

```powershell
WechatAutoOrganize.exe                  # 按配置（默认开浏览器）
WechatAutoOrganize.exe --no-open        # 这次不开
WechatAutoOrganize.exe --open           # 强制开（哪怕配置里关了）
WechatAutoOrganize.exe --port 8788      # 换端口
WechatAutoOrganize.exe --browser "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

实现细节：**等端口真能连上再开浏览器**（否则会先闪一个"无法访问"的空白页）；
指定浏览器不存在时打印原因并退回系统默认；启动日志会写明"自动开浏览器：是/否（哪个浏览器）"。

> 命令行里传带空格的路径要加引号，PowerShell 下写成 `--browser "C:\Program Files\...\chrome.exe"`。

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
6. **`.db-wal` 必须合并**（已实现 `wechat_todo/walsync.py`）：微信开着时新消息先写进
   `message_0.db-wal`，主库要等 checkpoint 才更新。只解主库会让「刚发的消息」完全不可见，
   表现为日志说「无新消息」。做法：拷出「源库 + -wal + -shm」到临时目录，用 SQLCipher 打开
   让 sqlite 自己重放 WAL，再 `sqlcipher_export` 导出标准明文。实测 19 个库、50MB 约 1.5 秒。
7. **不要用 PowerShell 5.1 的 `Get-Content -Raw` 改带中文的文件**：会按 GBK 解码再以 UTF-8 写回，把 `.gitignore` 里的规则粘到注释行上——`.env` 就这样差点被提交。

## 目录

```
wechat_todo/
  config.py     TOML + .env 装载，base_url/api_key 可配；缺配置直接报错退出
  decrypt.py    进程内 runpy 调上游采集脚本（冻结 exe 后 sys.executable 会套娃）
  walsync.py    用 SQLCipher 重放 -wal 后导出明文，否则读不到最新消息
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
