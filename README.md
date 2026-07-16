# nonebot-plugin-todaymonkeytop

记录 QQ 群中消息收到的 🐵 表情回应（`emoji_id=128053`），并按被贴表情的消息发送者生成当天排行榜。

## 功能

- 监听群消息，将 `message_id -> 发送者` 映射持久化到 SQLite；不保存消息正文或贴表情者列表。
- 监听 NapCat `group_msg_emoji_like` 通知，并用 NapCat `get_emoji_likes` 查询该消息当前完整的猴子回应数，避免重复通知或撤销回应造成误计数。
- 发送 `今日猴榜` 命令时，刷新截至当前时刻的数据并发送 TOP 10；命令前缀遵循 NoneBot 项目的 `command_start` 配置。
- 每日 `23:59`（`Asia/Shanghai`）向当天有消息记录的群自动发送 TOP 10。
- 记录消息映射、回应刷新、API 错误、查询与定时结算日志，便于排查。

## 安装

```bash
pip install nonebot-plugin-todaymonkeytop
```

在 NoneBot 项目的 `pyproject.toml` 中加载插件：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_todaymonkeytop"]
```

或使用 NB-CLI：

```bash
nb plugin install nonebot-plugin-todaymonkeytop
```

## 部署要求

- Python 3.10+、NoneBot2 2.5+、OneBot v11 适配器。
- 安装并可加载 `nonebot-plugin-apscheduler` 与 `nonebot-plugin-orm[sqlite]`（会由本插件声明的依赖自动安装）。
- NapCat 需开启群消息事件、`notice.group_msg_emoji_like` 以及扩展 API `get_emoji_likes`。

NapCat 的表情回应通知中的 `user_id` 是**贴表情的人**，而不是被贴表情的消息作者；插件会从已保存的消息映射中确定统计对象。若消息映射不存在，会尝试用 `get_msg` 回补。

部分 NapCat 版本可能只上报登录 QQ 自己的表情回应事件。此限制会导致插件无法即时感知其他成员的新回应；请在部署群中贴一次 🐵 表情并观察 `todaymonkeytop` 日志确认事件是否完整上报。

## 数据库迁移

本插件使用 NoneBot 官方推荐的 `nonebot-plugin-orm` 和 SQLite。首次安装、更新本插件模型后，在机器人项目目录执行：

```bash
nb orm upgrade
nb orm check
```

默认数据库文件为 NoneBot 数据目录中的 `nonebot-plugin-orm/db.sqlite3`。如需指定位置，在项目 `.env` 中配置：

```dotenv
SQLALCHEMY_DATABASE_URL=sqlite+aiosqlite:///path/to/data/todaymonkeytop.db
```

## 数据与日志

数据库保存 QQ 号、群号、消息 ID、显示名、日期、猴子回应数量与榜单发送标记，不保存消息正文或贴表情者列表。机器人重启后仍可读取当天已记录的数据；插件会在每日结算后清理超过 14 天的数据。

所有插件日志以 `todaymonkeytop:` 开头。将 NoneBot 日志级别设为 `DEBUG` 可看到每条已记录的群消息映射。
