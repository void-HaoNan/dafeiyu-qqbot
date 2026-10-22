# 模块说明

所有模块在 `data/dafeiyu/config.json` 的 `modules.<名字>.enabled` 里开关；
控制台「管理」页有同样的开关和一句话说明。改完要重启 AstrBot 才生效。

| 模块 | 作用 | 主要配置 |
|---|---|---|
| access | 黑白名单守门，白名单外的人和群不理 | 名单在 `data/access_lists.json`，控制台好友页改 |
| asr | 收到语音先转文字，避免主模型不认音频 | `modules.asr`：`model_dir`、`language`、`cpu_threads`、`auto_download` |
| card | 解析点歌插件发的音乐卡片 | 无 |
| face | 按情绪发/收表情包 | 无，素材在 `stickers/<情绪>/` |
| maintenance_guard | 维护模式压住回复，插件直接回维护提示 | `modules.maintenance`：`message`、`cooldown_seconds` |
| memory_scheduler | 群聊攒够 25 条或每 30 分钟整理进档案 | 固定阈值，改代码里的 `THRESHOLD`/`INTERVAL` |
| oneline | 把回复里的换行收掉，只发一条 | 无 |
| profile | 统一上下文：当前说话人、喜好、关系、群名单、维护补记 | 无，档案在 `data/dafeiyu/profiles.json` |
| qzone | AI 说说、草稿、每日日记 | 第三方空间插件的配置，控制台空间页改 |
| reply_gate | 群聊里先判断这句要不要接 | 固定 15 秒间隔，改代码里的 `MIN_GAP` |
| sendto | 让机器人把话发到别的群/私聊 | `modules.sendto`：`default_group`、`cooldown_seconds`、`max_per_minute` |
| stream | 收发消息写进 `logs/panel_stream.jsonl`，控制台和记忆都靠它 | 无 |
| voice | 按情绪调 GPT-SoVITS 发语音，RVC 唱歌 | `modules.voice`：`voice_enable`、`max_chars`、`use_gpt_sovits`、`emotion_ref_root` |

## 模块之间不互相调用

需要共享的东西放在 `dafeiyu_core`，模块只拿 `self.kernel`、`self.cfg` 和统一的
`panel_stream.jsonl` / `profiles.json`。

## 加一个模块

1. 在 `engines/v2/plugin/dafeiyu_modules/` 新建 `xxx.py`
2. 写一个 `@register` 的类，继承 `Module`，起 `name`
3. 实现需要的钩子：`on_message`、`on_waiting_llm_request`、`on_llm_request`、
   `on_llm_response`、`on_decorating_result`
4. 重启 AstrBot；控制台「管理」页会自动多出这张卡（需要把描述加进
   `panel/app.py` 的 `MODULE_CATALOG`）

不用改 `main.py`，也不用拆成新的 AstrBot 插件。
