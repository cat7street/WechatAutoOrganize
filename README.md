# WechatAutoOrganize

本地运行的微信聊天记录整理工具。从本机微信 4.x 客户端读取并解密聊天数据库，经 LLM 结构化抽取生成待办事项，输出 Markdown 日报，并提供本地 Web 面板进行查看、编辑与调度。

`Python 3.12+` · `Node.js 20+` · `Windows 10/11` · `WeChat 4.x` · 上游组件 `MIT`

> **数据边界**：解密、抽取、状态库与面板均运行在 `127.0.0.1`。唯一的外部网络请求发往用户自行配置的 OpenAI 兼容端点。仓库与运行时目录均不包含密钥、解密产物或聊天明文。

---

## 目录

- [项目简介](#项目简介)
- [核心能力](#核心能力)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [功能说明](#功能说明)
  - [媒体消息处理](#媒体消息处理)
  - [溯源、合并与提级](#溯源合并与提级)
  - [待办状态对账](#待办状态对账)
  - [定时任务](#定时任务)
  - [浏览器启动行为](#浏览器启动行为)
- [数据流与幂等](#数据流与幂等)
- [工程约束与已知限制](#工程约束与已知限制)
- [目录结构](#目录结构)
- [HTTP 接口](#http-接口)
- [安全与合规声明](#安全与合规声明)
- [第三方组件与许可](#第三方组件与许可)

---

## 项目简介

本工具面向「聊天记录里散落着待办」这一场景，将微信会话内容转换为可跟踪、可复核的任务清单。设计上遵循三条原则：

1. **数据不出本机**。除用户显式配置的 LLM 端点外，不产生任何外部网络请求；解密产物、状态库、导出文件均落在本地目录。
2. **不重写上游能力**。数据库密钥提取与图片解密直接调用上游开源实现（见 [第三方组件与许可](#第三方组件与许可)），本项目只负责编排、清洗、抽取与呈现。
3. **可复核**。每条待办都回指原文消息 ID，模型的每一次判断都要求附带逐字原文证据，规则层只校验可机器验证的条件。

## 核心能力

- **本机解密**：从运行中的微信进程读取数据库密钥，解密微信 4.x 的 SQLCipher 4 库，并合并 `-wal` 中尚未落盘的最新消息。
- **多类型消息处理**：文本、图片、文件、引用、合并转发、链接、转账、拍一拍、系统与撤回消息均纳入处理（详见 [媒体消息处理](#媒体消息处理)）。
- **结构化抽取**：按字符预算分批提交 LLM，以 JSON Schema 约束输出；模型给出的 `msg_ids` 必须落在本批消息集合内，否则整条丢弃。
- **三层去重**：消息级水位线、任务级哈希唯一索引、引用级回指校验（详见 [数据流与幂等](#数据流与幂等)）。
- **待办全生命周期对账**：新增、更新、取消、替代、恢复五类差异一次性判定，支持反复翻转并保留变更痕迹。
- **溯源与合并**：任一待办可回溯其前后文；同一事项的多条待办可由模型判定后合并。
- **内置定时任务**：不依赖系统计划任务，支持固定间隔与每日固定时刻，重叠轮次自动跳过。
- **本地面板**：概览、白名单、待办、运行、设置五个页面，基于 FastAPI + SSE 实时输出运行日志。

## 系统架构

```
微信进程内存 ──┐
              │  上游 wcdb-key-tool（原样调用，不重写）
              ▼
        decrypted/*.db  (SQLCipher 4 → 明文 SQLite，以只读 immutable 方式打开)
              ▼
        scan.py    白名单匹配 + 水位线增量 + 群聊前缀剥离 + zstd 解压 + 发送人映射
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
| 采集 | [`TANGandXUE/wcdb-key-tool`](https://github.com/TANGandXUE/wcdb-key-tool)（Windows `Config.Cipher` 运行时扫描）；源码按 MIT 许可固定快照内嵌于 `vendor/wcdb-key-tool/` |
| 后端 | Python 3.12 + FastAPI + SQLite（stdlib `sqlite3`，无 ORM、无容器依赖） |
| 模型 | 任意 OpenAI 兼容端点，`base_url` / `api_key` / `model` 全部可配置 |
| 前端 | React 19 + TypeScript + Vite + Tailwind 4 + 本地 shadcn 风格组件 + TanStack Query + ECharts |
| 类型 | 由 `/openapi.json` 经 `openapi-typescript` 生成，前后端字段保持一致 |
| 打包 | PyInstaller 单文件 exe（前端 dist 内嵌） |

## 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 |
| 微信 | 4.x，**已登录并保持运行**（密钥提取需读取活动进程内存） |
| Python | 3.12 |
| Node.js | 20+，包管理器使用 `pnpm` |
| 上游脚本 | 已随仓库内嵌于 `vendor/`，无需另行下载 |

### 实测环境

| 项 | 值 |
|---|---|
| 微信 | **4.2.0.11**（Windows，`Weixin.exe`） |
| 解密 | **20/20 个库全部命中**，`integrity_check = ok`，0.2 GB |
| 权限 | 读取进程内存提取密钥**不需要管理员权限**（同用户进程） |
| 数据规模 | 183 个会话 / 94 张 `Msg_*` 表 / 80,438 条消息 |

## 快速开始

### 1. 前置

- Windows 环境下微信 4.x 已登录并保持运行。
- 上游采集脚本**已随仓库内嵌**：`vendor/wcdb-key-tool/`（数据库密钥与全库解密）、`vendor/wechat-decrypt/`（图片密钥与 `.dat` 解密），各自附带上游 `LICENSE` 与 `UPSTREAM.md`。上游项目存在被归档或删除的风险（见 [安全与合规声明](#安全与合规声明)），因此不再依赖线上拉取，此步骤无需额外操作。

### 2. 后端

```powershell
python -m venv tools\venv
tools\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.toml config.toml
Copy-Item .env.example .env      # 然后填写 base_url / api_key / model
```

### 3. 前端

```powershell
cd frontend
pnpm install
pnpm gen:api        # 后端运行时刷新 TS 类型
pnpm build
```

### 4. 运行

```powershell
# 列出全部会话，用于填写白名单
tools\venv\Scripts\python.exe scripts\run.py --list-sessions

# 干跑：只组装请求包到 data/prompt_dump.json，不产生模型调用费用
tools\venv\Scripts\python.exe scripts\run.py --skip-decrypt --dry-run

# 正式运行一次增量
tools\venv\Scripts\python.exe scripts\run.py

# 启动面板
tools\venv\Scripts\python.exe scripts\serve.py --open
```

### 5. 打包为 exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 产物 dist\WechatAutoOrganize.exe（约 22 MB），双击即启动面板
```

## 配置说明

配置来源优先级为：**环境变量 > `.env` > `config.toml`**。非敏感项置于 `config.toml`，密钥置于 `.env`。

```toml
[llm]
base_url = "https://api.deepseek.com/v1"   # 可替换为任意 OpenAI 兼容端点
model = "deepseek-chat"
response_format = "json_object"            # 端点支持结构化输出时可改为 json_schema

[filter]
whitelist = ["示例群名", "wxid_xxxx", "12345678@chatroom"]  # username / 备注名 / 群 ID 片段
window_hours = 48
```

```ini
# .env
WECHAT_TODO_BASE_URL=https://api.deepseek.com/v1
WECHAT_TODO_API_KEY=sk-xxx
WECHAT_TODO_MODEL=deepseek-chat
```

面板「设置」页同样可以修改上述配置。**`api_key` 只写不读**：保存后界面仅显示 `sk-x****abcd` 形式的掩码，明文只落盘到 `.env`。

## 功能说明

### 媒体消息处理

微信 4.x 将 appmsg 子类型**打包进 `local_type` 高 32 位**：`base = local_type & 0xFFFFFFFF`，`subtype = local_type >> 32`。各类型处理方式如下：

| 消息 | base/subtype | 处理方式 | 状态 |
|---|---|---|---|
| 文本 | 1 | 直接读取 | 支持 |
| 图片 | 3 | 解 `.dat` 为图片后作为图像输入多模态模型 | 支持，受下述限制约束 |
| 语音 | 34 | 仅提供时长；本机无音频文件，不做转写 | 仅元信息 |
| 视频 | 43 | 时长元信息 | 仅元信息 |
| 文件 | 49/6 | 按 md5 定位后本地抽取正文（xlsx/docx/pdf/txt/csv/md） | 支持 |
| 引用 | 49/57 | 被引用原文与当前回复拼接为一条 | 支持 |
| 合并转发 | 49/19 | 解析 `recorditem` 中的完整聊天记录 | 支持 |
| 链接 / 小程序 / 视频号 | 49/5, 33, 36, 51, 62 | 标题 + 描述 + URL | 支持 |
| 转账 / 红包 | 49/2000, 2001 | 金额与说明 | 支持 |
| 拍一拍 | 49/* | 单独归类，不计入链接统计 | 支持 |
| 表情 | 47 | 丢弃（无信息量） | 不支持 |
| 系统 / 撤回 | 10000 | 撤回内容作为上下文保留 | 支持 |

一轮实测（2 个白名单会话）：4,302 条消息中提取出 **121 张图片、171 条引用、12 条语音、9 条视频、2 条合并转发、13 条钱款记录**，切分为 15 批请求。

#### 媒体层的三项限制

1. **原图为微信自有 WXGF 容器（HEVC）**，并非 JPEG。`<md5>.dat` 解出的文件头为 `wxgf`，PIL 无法打开；可直接使用的是 `<md5>_t.dat` 缩略图（120–700 px）。代码按 `_t → 原图 → _h` 顺序逐变体尝试，以 PIL 能否打开为唯一判定依据；全部失败时返回空结果并记录原因，**不会将原始 `.dat` 作为图片提交给模型**。
2. **部分历史图片的 `.dat` 已被微信清理缓存**：抽样 80 条图片消息，33 条可解，47 条在本机不存在对应文件。此为数据缺失，非代码缺陷。
3. **语音消息没有本地音频**：63 条语音消息对应的 `.silk` / `.amr` / `.mp3` 文件在全盘范围内均为 0 个。如需转写，须先触发微信下载语音或从 CDN 拉取，属独立议题。

图片密钥与数据库密钥不同（V2 格式为 AES-128-ECB + XOR），需从进程内存另行提取。本项目复用上游 `find_image_key.py`（`vendor/wechat-decrypt/`），密钥写入运行时就位目录 `data/upstream/config.json`，并缓存至 `data/image_keys.json`。

### 溯源、合并与提级

| 能力 | 入口 | 说明 |
|---|---|---|
| 查看待办出处 | 待办页「看上下文」 | 拉取该待办所引用消息的前后 6 条，命中原文标注「待办依据」；支持深链 `/tasks?task=<id>` |
| 浏览完整会话 | 会话白名单页「查看」 | 自最新消息向前翻页，每次 60 条，可持续加载更早内容 |
| 模型合并同一事项 | 待办页「AI 合并去重」/ `POST /api/merge` | 由模型判定哪些条目属于同一事项；合并后 `msg_ids` 取并集，被吸收条目置为 `status=merged` |
| 反复提及自动提级 | 自动 | `mentions` 为合并后消息条数：≥2 条至少为 `medium`，≥5 条为 `high` |

最终优先级取「模型判断」与「提及次数下限」两者中的较高者，因此规则可复算、可解释：

```python
def floor_by_mentions(n):
    return "high" if n >= 5 else "medium" if n >= 2 else "low"
```

一轮实测：79 条待办聚合为 6 组，吸收 8 条，提级 10 条；其中被提及次数最多的一条（10 次）由模型给出的低档位抬升至 `high`。

### 待办状态对账

待办的生命周期不因抽取而固定：后续对话可能推翻、替代或重新提起某项待办。

**实现方式**：将整段时间窗（默认 48 小时）的完整对话一次性提交模型，由模型逐条判定状态。不采用「每条待办配一个小窗口」的做法——那等同于由人预先筛除证据，而推翻结论的依据往往出现在距离待办较远的位置。

模型输出结构化判定：

```json
{"decisions":[{"task_id":73,"final_state":"pending|cancelled|superseded",
  "replacement":"新方案是什么","evidence_quote":"逐字原文",
  "decided_at_msg_id":6521,"confidence":0.8}]}
```

规则层**只校验可机器验证的条件**，不推断语义：

| 校验项 | 规则 |
|---|---|
| `decided_at_msg_id` | 必须落在时间窗内，防止编造判定位点 |
| `evidence_quote` | 必须逐字出自时间窗内的原文，防止改写证据 |
| 证据长度 | 最短 4 个字符，过短引用（如「换」「行」）予以驳回 |
| `confidence` | 低于 0.6 时仅标注「疑似…」，不修改状态，留待人工确认 |
| 终态取值 | 非法终态直接驳回 |

**反复提及按终态处理**：`pending` 与 `cancelled` 可双向变更，每次变更留痕于 `state_note`；判定回到 `pending` 时清除旧的取消痕迹，避免残留信息误导。

实测样例（37 条待办 / 4 次调用；条目内容已替换为示例写法）：

```
#12 续费 <示例项目> 的服务器
   首轮判定 superseded（依据「已下单新服务器」）
   但后文出现 #1001「本月无法完成迁移」、#1002「备案仍绑定在原机器上」
   重判 → pending，备注「核对后确认仍要做（迁移未成，仍需续费）」
#41 跟进 <事项 A>  → cancelled（依据 msg1003「暂时不用处理」）
#39 修复 <事项 B>  → 疑似 cancelled（置信度不足，仅标注）
```

配置开关：

```toml
[reconcile]
enabled = true
window_hours = 48          # 参与判定的对话时长
max_context_chars = 160000 # 超出时自最早内容截断，保留最新
tasks_per_call = 12        # 每次调用携带的待办条数
max_calls = 8
```

### 定时任务

在面板「设置 → 定时任务」中配置，保存即生效，无需手工编辑配置文件。一轮完整流程为：

```
解密 → WAL 合并 → 48h 全量对账 → 合并去重 → 输出当天 Markdown
```

**定时任务一律采用「全量对账」（`mode = "full"`）**：将最近 `window_hours` 的完整对话与当前待办清单一并提交模型，由其一次性输出全部差异。

提交给模型的清单中，每条待办均携带依据 `msgid`，对话内对应消息以 `★` 标注，便于模型将待办与原文对应；若某条待办的依据早于时间窗，则单独提取至「早于时间窗的待办原始依据」区块，避免模型在缺少证据的情况下判断。

差异类型：

| 差异 | 说明 |
|---|---|
| `new` | 对话中新出现、清单中不存在的待办 |
| `update` | 修改已有待办的标题 / 截止时间 / 责任人 / 优先级（如「改成下周一」） |
| `cancel` | 明确不再执行 |
| `supersede` | 被新方案取代（如「换新服务器」「改成私有部署」） |
| `restore` | 此前判定取消、后续重新需要执行 |

仅执行增量抽取会导致失真：模型无法看到全局上下文，既无法发现「后来不要了」，也无法修改已有字段。

面板可配置项：

| 项 | 说明 |
|---|---|
| 固定间隔（分钟） | `0` 表示不使用间隔 |
| 每日固定时刻 | 逗号分隔，如 `09:00,21:30`；与间隔可同时设置，取最近一次 |
| 运行模式 | 全量对账（推荐）/ 增量抽取 |
| 跳过重新解密 | 定时轮次跳过解密，WAL 仍会合并，速度更快 |
| 保存 / 启用 / 停用 | 立即生效，写入 `config.toml` |
| 立即运行一轮 | 手动触发，无需等待调度时刻 |

面板卡片实时显示下次运行时间、上次运行时间、上次结果与已运行 / 已跳过次数。

对应配置：

```toml
[pipeline]
mode = "incremental"   # 手动运行默认值；定时任务默认走 full

[schedule]
enabled = true
mode = "full"
interval_minutes = 0          # 0 = 不使用间隔，仅按固定时刻
daily_times = ["09:00", "21:30"]
skip_decrypt = false          # 定时轮次跳过重新解密（WAL 照样合并，更快）
run_on_start = true
```

实测结果：

- 调度器以 60 秒间隔触发一轮，`last_result=成功`，单轮消耗 83.7k tokens；停用后 `next_at=null`。
- 一轮全量对账（时间窗 48 小时、1,784 条消息、3 次调用）：**新增 3 条、更新 6 条、替代 1 条、恢复 1 条**，耗时约 7 分钟。
- 上一轮尚未完成时，下一轮自动**跳过**而非排队堆积，状态中的 `skipped` 计数递增。

> 单轮全量对账耗时数分钟（对话较长、模型响应较慢），建议间隔设置为 30–60 分钟。
> 需要更快时可将 `skip_decrypt` 设为 `true`，WAL 合并仍会补齐最新消息。

### 浏览器启动行为

启动面板时自动打开浏览器，相关配置如下：

```toml
[server]
host = "127.0.0.1"
port = 8787
open_browser = true          # 启动时是否自动打开浏览器
browser = ""                 # 留空使用系统默认；也可指定具体浏览器
# browser = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

命令行参数优先级高于配置文件：

```powershell
WechatAutoOrganize.exe                  # 按配置执行（默认打开浏览器）
WechatAutoOrganize.exe --no-open        # 本次不打开
WechatAutoOrganize.exe --open           # 强制打开（即使配置中已关闭）
WechatAutoOrganize.exe --port 8788      # 指定端口
WechatAutoOrganize.exe --browser "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

实现细节：仅当端口确实可连接后才打开浏览器，避免先出现「无法访问」的空白页；指定的浏览器不存在时打印原因并回退至系统默认；启动日志会记录「自动打开浏览器：是/否（具体浏览器）」。

> 命令行传入含空格的路径需加引号，PowerShell 下写法为 `--browser "C:\Program Files\...\chrome.exe"`。

## 数据流与幂等

| 层 | 机制 |
|---|---|
| 消息级 | `cursor` 表按会话记录 `sort_seq`（毫秒）水位线，只读取新消息 |
| 任务级 | `sha256(会话 + 归一化标题 + 截止时间 + 责任人)` 唯一索引，重复记录直接丢弃 |
| 引用级 | LLM 输出的 `msg_ids` 必须落在本批消息集合内，否则整条丢弃（防幻觉） |

## 工程约束与已知限制

以下约束均由实测结果确定，并已落实到代码实现中：

1. **解密目录必须以 `mode=ro&immutable=1` 打开**。一旦以读写模式打开过，SQLite 会留下零长 `-wal` 与 `-shm` 文件，此后所有查询都会读到**空 schema（0 张表）**。`decrypt.py` 在每次解密后清理 sidecar 文件。
2. **`sort_seq` 为毫秒，`create_time` 为秒**。以秒值作为毫秒水位线会使时间窗完全失效（早期版本因此抽取到了 48 小时窗口之外的消息）。
3. **群聊文本自带 `发送人:\n` 前缀**，提交模型前必须剥离。
4. **非文本消息体为 zstd 压缩**（magic `28 b5 2f fd`），仅文本类型（`local_type=1`）为明文。
5. **表名规则为 `Msg_<md5(username)>`**，需通过 `session.db.SessionTable.username` 计算 md5 后匹配（183 个会话命中 94 张表，其余会话确无消息）。
6. **`.db-wal` 必须合并**（已实现于 `wechat_todo/walsync.py`）：微信运行时新消息先写入 `message_0.db-wal`，主库需等待 checkpoint 才更新，仅解密主库会导致「刚发送的消息」完全不可见，表现为日志提示「无新消息」。做法为将「源库 + `-wal` + `-shm`」复制到临时目录，由 SQLCipher 自行重放 WAL，再通过 `sqlcipher_export` 导出标准明文。实测 19 个库、50 MB 数据耗时约 1.5 秒。
7. **不要使用 PowerShell 5.1 的 `Get-Content -Raw` 修改含中文的文件**：该命令按 GBK 解码后以 UTF-8 写回，会将 `.gitignore` 中的规则并入注释行。

已知限制：

- 语音消息仅提供时长元信息，未做转写（本机无音频文件，见 [媒体消息处理](#媒体消息处理)）。
- 原图为 WXGF 容器，可用的是缩略图；部分历史图片的 `.dat` 已被微信清理。
- 群公告式刷屏依赖白名单过滤，不在提示词层做兜底。
- 前端单 chunk 约 1.5 MB（概览页引入 ECharts），后续可按路由懒加载拆包。
- 抽取质量取决于模型：结构化输出与强制 `msg_ids` 回指原文是目前最有效的降幻觉手段。

## 目录结构

```
wechat_todo/
  config.py     TOML + .env 装载，base_url / api_key 可配置；缺少配置时直接报错退出
  decrypt.py    进程内以 runpy 调用上游采集脚本（冻结为 exe 后子进程路线会递归调用自身）
  walsync.py    使用 SQLCipher 重放 -wal 后导出明文，否则无法读取最新消息
  scan.py       只读读取数据库：会话列表、白名单、增量消息、归一化
  extract.py    提示词与分批
  llm.py        OpenAI 兼容客户端 + JSON 结构化输出
  store.py      水位线 / 待办 / 运行记录 / token 记账
  render.py     Markdown 日报
  pipeline.py   iter_run() 单一生成器，CLI 与 SSE 共用
  server.py     FastAPI + SSE + 静态资源托管
frontend/       React 面板（概览 / 白名单 / 待办 / 运行 / 设置）
scripts/        run.py（CLI）、serve.py（单进程）、build_exe.ps1
vendor/         上游脚本固定快照（MIT）：wcdb-key-tool（数据库密钥与解密）、wechat-decrypt（图片）
```

## HTTP 接口

面板与 CLI 共用同一套接口，接口清单由 `/openapi.json` 生成：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查与解密状态 |
| `GET` | `/api/settings` | 读取当前配置 |
| `POST` | `/api/settings/llm` | 更新 LLM 端点配置（密钥只写不读） |
| `POST` | `/api/settings/whitelist` | 更新会话白名单 |
| `POST` | `/api/settings/media` | 更新媒体处理开关 |
| `GET` | `/api/sessions` | 会话列表 |
| `GET` | `/api/sessions/{username}/messages` | 指定会话的消息分页 |
| `POST` | `/api/decrypt` | 执行一次全库解密 |
| `POST` | `/api/run` | 触发一轮流水线 |
| `GET` | `/api/run/events` | 运行进度（SSE） |
| `GET` | `/api/tasks` | 待办列表 |
| `GET` / `PATCH` | `/api/tasks/{task_id}` | 待办详情与状态修改 |
| `GET` | `/api/tasks/{task_id}/context` | 待办前后文（溯源） |
| `POST` | `/api/merge` | 语义合并去重 |
| `GET` / `POST` | `/api/schedule` | 读取 / 更新定时计划 |
| `GET` | `/api/stats` | 统计概览 |
| `GET` | `/api/runs` | 运行历史 |

未匹配的路径回落至前端单页应用（由同一进程托管静态资源）。

## 安全与合规声明

- 本工具仅用于处理**本人账号、本人设备**上的数据，不应在他人设备上运行。
- 密钥提取通过读取同用户微信进程内存实现，**不需要管理员权限**；工具不修改微信的程序文件、数据库文件与配置。
- 解密能力来自上游开源项目（`TANGandXUE/wcdb-key-tool`、`328336690/wechat-decrypt`，均为 MIT 许可）。两份代码已按许可**固定快照内嵌**于 `vendor/`，各自附带 `LICENSE` 与 `UPSTREAM.md`（含来源 URL、快照日期、收录范围与升级方式），上游代码未作任何修改。上游 `chatlog`、`PyWxDump` 已于 2025-10 被发函删库，同类项目存在随时归档的可能，因此仓库自持一份快照，不依赖线上上游。
- 仓库内不包含任何密钥、解密产物或聊天明文。`.env`、`config.toml`、`keys.json`、`decrypted/`、`data/`、`out/`、`tools/` 均列入 `.gitignore`；`vendor/` 仅含源码与许可，不含 `config.json` / `all_keys.json`（已在 `.gitignore` 中单独排除）。
- 微信 Windows 4.x 路线的合规风险请自行评估。

## 第三方组件与许可

| 组件 | 来源 | 许可 | 用途 | 快照位置 |
|---|---|---|---|---|
| wcdb-key-tool | [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) | MIT © 2025 CloudDreamAI / TANGandXUE | 数据库密钥提取与全库解密 | `vendor/wcdb-key-tool/` |
| wechat-decrypt | [328336690/wechat-decrypt](https://github.com/328336690/wechat-decrypt) | MIT © 2026 328336690 | 图片密钥提取与 `.dat` 解密 | `vendor/wechat-decrypt/` |

两个组件均按其原始许可分发，许可全文见各自目录下的 `LICENSE`，来源与快照信息见 `UPSTREAM.md`。`vendor/` 之外的仓库自有代码，其许可证以仓库所有者的声明为准（当前仓库未附 `LICENSE` 文件）。
