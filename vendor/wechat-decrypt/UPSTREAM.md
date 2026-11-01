# 上游快照：wechat-decrypt

- 来源：<https://github.com/328336690/wechat-decrypt>（v1.0.0，CHANGELOG 记 2026-06-05）
- 许可：MIT（见同目录 `LICENSE`，Copyright (c) 2026 328336690）
- 快照日期：2026-11-01
- 取用方式：仓库源码整份拷入，**未改一行代码**

## 为什么把上游代码放进仓库

图片 `.dat` 解密与图片 key 内存扫描都走这份脚本（`wechat_todo/media.py` 按路径加载，
`wechat_todo/upstream.py` 负责就位到 `data/upstream/`）。上游一旦归档或被删库，
媒体能力立刻失效，所以按 MIT 许可留一份固定快照，随仓库自持。

## 本仓库实际调用的文件

| 文件 | 用途 |
|---|---|
| `find_image_key.py` | 从微信进程内存扫图片 AES/XOR key |
| `decode_image.py` | `.dat` → 图片（V1 XOR / V2 AES-128-ECB） |

其余脚本（`decrypt_db.py`、`find_all_keys.py`、`monitor*.py`、`mcp_server.py`、
`latency_test.py`、`find_image_key_monitor.py`）本仓库不调用，随快照保留，
上游失联时仍可单独使用。

## 刻意没有收的

- `config.json`、`all_keys.json`：本机跑出来的真实图片/数据库密钥，绝不入库。
  运行时由 `wechat_todo/upstream.py` 在 `data/upstream/config.json` 生成。
- `decoded_images/`、`__pycache__/`、`.github/`、上游 `.gitignore`：运行产物与 CI 配置。

## 更新方式

```powershell
curl.exe -L -o wd.tgz https://codeload.github.com/328336690/wechat-decrypt/tar.gz/refs/heads/main
tar -xzf wd.tgz
# 逐文件 diff 确认差异后覆盖本目录，并把上面的「快照日期」改成当天
```
