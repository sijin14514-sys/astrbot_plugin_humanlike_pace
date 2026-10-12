# 真人节奏 · astrbot_plugin_humanlike_pace

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.24.0-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

让 Bot 不再秒回、不再逢消息必答。

真人聊天有两个机器人不会有的习惯：**收到表情包有时候根本不回**，以及**回之前会停一下**。这个插件补上这两点。

## 功能

### 1. 已读不回

收到**纯图片消息**（表情包）时，按概率直接终止事件 —— 不调用模型，也不回复。

- 只对不含文字的消息生效。图片配了文字的照常回复。
- 带冷却时间，避免连续几条表情包全部石沉大海。
- 终止发生在调用模型之前，所以不理人的时候**不消耗 token**。

### 2. 打字延迟

回复发送前按正文长度停顿：

```
停顿 = 基础停顿 + 正文字数 × 每字停顿 + 随机抖动   （再按上限截断）
```

默认参数下，10 个字的回复大约停 1.8~2.6 秒，30 个字大约 2.9~3.7 秒。纯表情包回复也会停一小下，避免同步发出。

## 安装

### 从 GitHub 安装

```bash
cd <AstrBot 数据目录>/data/plugins
git clone https://github.com/sijin14514-sys/astrbot_plugin_humanlike_pace
```

然后重启 AstrBot，或在 WebUI 插件页重载。

### 手动安装

下载仓库压缩包，解压后把 `astrbot_plugin_humanlike_pace` 整个目录放到 `<AstrBot 数据目录>/data/plugins/` 下。

无额外依赖，不需要 `pip install`。

## 配置

| 配置段 | 键 | 默认 | 说明 |
| --- | --- | --- | --- |
| `silence` | `enable` | `true` | 开关已读不回 |
| | `probability` | `45` | 收到纯图片消息时不理人的概率（%）。0 = 永远回，100 = 永远不回 |
| | `cooldown_seconds` | `90` | 两次沉默的最小间隔，防止连续多次不理人。0 表示不限制 |
| `delay` | `enable` | `true` | 开关打字延迟 |
| | `base_seconds` | `1.0` | 每条回复至少停顿 |
| | `per_char_seconds` | `0.05` | 正文每多一个字增加的停顿 |
| | `jitter_seconds` | `0.8` | 在 0 到这个值之间随机追加 |
| | `max_seconds` | `6.0` | 停顿上限 |

## 为什么有这个插件

写它是因为现有的同类插件在新的 AstrBot 上已经跑不起来了。

[`astrbot_plugin_humanlike`](https://github.com/anka-afk/astrbot_plugin_humanlike) 做的正是这两件事，思路很好，但最后一次提交停在 2025 年 3 月。它用的 `filter.EventMessageType.ALL` 在 AstrBot 4.24 之后已经不存在了（现在只有 `GROUP_MESSAGE` / `PRIVATE_MESSAGE` / `OTHER_MESSAGE`），安装后会直接加载失败。

另外几个同类插件要么偏群聊唤醒逻辑，要么体量过大。所以重写了一个只做这两件事、在新版本上验证过的版本。

## 实现说明

- **已读不回**用 `event.stop_event()` 终止事件传播，处理器优先级设为 `100`（越大越先执行），确保在调用模型之前就结束。
- **打字延迟**挂在 `on_decorating_result` 上，优先级 `-100`，排在其它改写消息链的插件之后，这样统计到的是最终正文长度。
- 统计长度时会剔除 `&&分类&&` 形式的标记（[表情包管理器](https://github.com/anka-afk/astrbot_plugin_meme_manager) 用它来发表情包），避免标记被算进字数。
- 停顿用 `asyncio.sleep`，不阻塞事件循环。多会话并发时互不影响。

## 常见问题

**为什么带文字的图片消息不会触发已读不回？**
因为那种消息通常带实际内容，不理人容易显得莫名其妙。只对"纯发表情包"这种低信息量消息生效，更接近真人习惯。

**为什么设了上限？**
正常聊天不会出现需要停 20 秒的情况，上限是为了防止异常长的正文（比如误触发了长回复）把消息卡住。

**和表情包插件冲突吗？**
不冲突。已读不回的判断只看收到的消息，打字延迟在发送前统计长度，两者都不改写消息内容。

## License

[MIT](LICENSE)
