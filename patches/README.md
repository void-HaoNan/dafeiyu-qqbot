# AstrBot 核心补丁（4.29.1）

AstrBot 原版有两个地方不满足本项目需求，这里放的是改好的完整文件。
只适用于 AstrBot **4.29.1**，升级后要重新对照修改。

## 改了什么

### group_chat_context.py

路径：`astrbot/builtin_stars/astrbot/group_chat_context.py`

- 群图片描述前，把本地路径或 http 图片转成 data URL，智谱视觉只认这种
- GIF / WEBP / 超过 3MB 的图，先取首帧压到 1600 像素以内转成 jpg，否则智谱回 1210
- 顺手修了原文件里模块级函数插在 class 中间、把后面方法吞掉的问题
- 群上下文块标 `_no_save`，当成临时提示，不写进会话历史（不然每轮多一块，越滚越大）

### whitelist_check/stage.py

路径：`astrbot/core/pipeline/whitelist_check/stage.py`

- 白名单 stage 每次处理消息前按 `cmd_config.json` 的修改时间热加载，
  控制台改名单后立即生效，不用重启 AstrBot

### waking_check/stage.py

路径：`astrbot/core/pipeline/waking_check/stage.py`

- 空消息（QQ 系统事件）不再取 `messages[0]` 报 IndexError
- 这个文件可能被别的插件改过，所以用 `fix_waking_stage.py` 只加一行 `messages and`，
  不整文件覆盖，避免把别人的跟随输入逻辑冲掉

## 怎么打

```powershell
# 默认装到 D:\QQBot\AstrBot
powershell -ExecutionPolicy Bypass -File .\apply_core_patches.ps1

# 装在别处
powershell -ExecutionPolicy Bypass -File .\apply_core_patches.ps1 -AstrBotRoot E:\QQBot\AstrBot
```

脚本会：

1. 检查 `pyproject.toml` 版本是不是 4.29.1（不是会提示，但可以继续）
2. 把原文件备份成 `.bak`（已存在就不覆盖）
3. 覆盖两个文件
4. 再跑 `fix_waking_stage.py`，只给空消息处加一行 `messages and`

## 怎么退

把同目录的 `.bak` 改回原名覆盖回去；空消息保护把 `stage.py.bak` 覆盖回 `stage.py`，重启 AstrBot。
