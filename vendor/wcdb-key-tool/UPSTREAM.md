# 上游快照：wcdb-key-tool

- 来源：<https://github.com/TANGandXUE/wcdb-key-tool>
- 许可：MIT（见同目录 `LICENSE`，Copyright (c) 2025 CloudDreamAI / TANGandXUE）
- 快照日期：2026-11-01
- 取用方式：`main` 分支源码整份拷入，**未改一行代码**

## 为什么把上游代码放进仓库

解密能力全靠这份脚本。上游 `chatlog`、`PyWxDump` 已于 2025-10 被发函删库，
同类项目随时可能归档或消失；一旦上游没了，本仓库的「解密 → 抽取」主流程直接断在第一环
（这正是 README 旧版「本仓库不包含其代码」埋的雷）。按 MIT 许可保留一份固定快照，
仓库自持，不再依赖线上上游。

## 收进来的文件

| 文件 | 用途 |
|---|---|
| `wcdb_key_tool_windows.py` | Windows 主入口；`wechat_todo/decrypt.py` 用 `runpy` 进程内调用 |
| `wcdb_key_tool.py` | 通用实现（上游同源） |
| `wcdb_key_tool_macos.py` | macOS 实现，本仓库暂未调用，随快照保留 |
| `README.md` / `LICENSE` | 上游文档与许可原文 |

## 刻意没有收的

- 上游 `.gitignore`、`.github/`：与本仓库构建无关。
- 任何运行产物：`all_keys.json`、`config.json`、解密出的 `*.db`——含真实密钥或聊天明文，绝不入库。

## 更新方式

```powershell
curl.exe -L -o wcdb.tgz https://codeload.github.com/TANGandXUE/wcdb-key-tool/tar.gz/refs/heads/main
tar -xzf wcdb.tgz
# 逐文件 diff 确认差异后覆盖本目录，并把上面的「快照日期」改成当天
```
