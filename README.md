# 真人节奏 · astrbot_plugin_humanlike_pace

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.24.0-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

![演示](demo.gif)

让 Bot 不再秒回、不再逢消息必答、不再打断你打字。

真人聊天和机器人有四个明显区别：**会等你把话说完**、**收到表情包有时候根本不回**、**回之前会停一下**、**停的时候对方能看到「正在输入」**。这个插件把这四件事补上。

## 功能

### 1. 消息防抖

收到消息后先等一小段时间（默认 2.5 秒）。期间如果又来新消息，就继续等，最后把这几条**合并成一条**再处理。

- 解决「你还在打第二条，她已经回了第一条」。
- 合并期间被吃掉的旧消息**不会各自触发一次模型调用**，所以也不会多花 token。
- 模型看到的是合并后的完整内容，也只回复一次。
- 把 `listen_seconds` 设为 0 等于关闭。

### 2. 已读不回

收到**纯图片消息**（表情包）时，按概率直接终止事件 —— 不调用模型，也不回复。

- 只对不含文字的消息生效。图片配了文字的照常回复。
- 带冷却时间，避免连续几条表情包全部石沉大海。
- 终止发生在调用模型之前，所以不理人的时候**不消耗 token**。

### 3. 打字延迟

回复发送前按正文长度停顿：

```
停顿 = 基础停顿 + 正文字数 × 每字停顿 + 随机抖动   （再按上限截断）
```

默认参数下，10 个字的回复大约停 1.8~2.6 秒，30 个字大约 2.9~3.7 秒。纯表情包回复也会停一小下，避免同步发出。

### 4. 正在输入

停顿期间向平台发送「正在输入」状态，让这段等待**看得见**。

- 走 OneBot v11 的扩展动作 `set_input_status`，需要协议端实现（[NapCat](https://github.com/NapNeko/NapCatQQ) 已实现）。
- **只支持私聊**——这是协议端本身的限制，群聊不会发送输入状态。
- 停顿短于 `min_seconds` 时不发，避免状态闪一下就消失。
- 平台不支持时**静默跳过**，不影响回复本身。

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
| `debounce` | `enable` | `true` | 开关消息防抖 |
| | `listen_seconds` | `2.5` | 收到消息后等待多久，期间有新消息就继续等。0 表示关闭 |
| `silence` | `enable` | `true` | 开关已读不回 |
| | `probability` | `45` | 收到纯图片消息时不理人的概率（%）。0 = 永远回，100 = 永远不回 |
| | `cooldown_seconds` | `90` | 两次沉默的最小间隔，防止连续多次不理人。0 表示不限制 |
| `delay` | `enable` | `true` | 开关打字延迟 |
| | `base_seconds` | `1.0` | 每条回复至少停顿 |
| | `per_char_seconds` | `0.05` | 正文每多一个字增加的停顿 |
| | `jitter_seconds` | `0.8` | 在 0 到这个值之间随机追加 |
| | `max_seconds` | `6.0` | 停顿上限 |
| `typing` | `enable` | `true` | 开关正在输入状态 |
| | `min_seconds` | `0.6` | 停顿短于该值时不发送输入状态 |

全部参数可在 AstrBot WebUI 插件设置页调整。

## 平台支持

| 功能 | 适用范围 |
| --- | --- |
| 消息防抖 | 所有平台 |
| 已读不回 | 所有平台 |
| 打字延迟 | 所有平台 |
| 正在输入 | 实现了 OneBot v11 `set_input_status` 的协议端，且仅私聊 |

前三项不依赖任何平台特性。第四项会先检查事件是否来自私聊、是否持有可调用动作的客户端对象，任一条件不满足就直接返回，不会抛错。

## 为什么有这个插件

写它是因为现有的同类插件在新版 AstrBot 上已经跑不起来了。

[`astrbot_plugin_humanlike`](https://github.com/anka-afk/astrbot_plugin_humanlike) 做的是其中两件事，思路很好，但最后一次提交停在 2025 年 3 月。它用的 `filter.EventMessageType.ALL` 在 AstrBot 4.24 之后已经不存在了（现在只有 `GROUP_MESSAGE` / `PRIVATE_MESSAGE` / `OTHER_MESSAGE`），安装后会直接加载失败。

另外几个同类插件要么偏群聊唤醒逻辑，要么体量过大；消息防抖则通常和一堆群聊功能绑在一起。所以重写了一个只做这几件事、在新版本上验证过的版本。

## 实现说明

- **消息防抖**用 `@filter.event_message_type(priority=200)` 抢在其它处理之前。最新一条消息负责等待与合并，等待期间被合并的旧事件在这里 `stop_event()`，所以在进入模型调用之前就被拦下了。
- **已读不回**同样用 `event.stop_event()` 终止事件传播，处理器优先级设为 `100`（越大越先执行）。
- **打字延迟**挂在 `on_decorating_result` 上，优先级 `-100`，排在其它改写消息链的插件之后，这样统计到的是最终正文长度。
- **正在输入**通过事件对象上的 `bot.call_action()` 调用协议端动作。AstrBot 的 `event.send_typing()` 目前是空实现，所以这里直接走动作调用；调用被包在 `try` 里，协议端没实现时只记一次日志。
- 统计长度时会剔除 `&&分类&&` 形式的标记（[表情包管理器](https://github.com/anka-afk/astrbot_plugin_meme_manager) 用它来发表情包），避免标记被算进字数。
- 等待与停顿都用 `asyncio.sleep`，不阻塞事件循环。多会话并发时互不影响。

## 常见问题

**消息防抖会不会让我等很久？**
每条消息至少会多等 `listen_seconds` 这么久。设成 2.5 秒时，加上打字延迟通常总共 4~6 秒才收到回复。如果觉得慢，可以调小，或者只保留打字延迟。

**防抖和打字延迟会不会叠加得太慢？**
会叠加。两个都在模拟"真人需要时间"，但角度不同：防抖是"等你说完"，延迟是"我在打字"。如果只想要其中一个，关掉另一个即可。

**为什么带文字的图片消息不会触发已读不回？**
因为那种消息通常带实际内容，不理人容易显得莫名其妙。只对"纯发表情包"这种低信息量消息生效，更接近真人习惯。

**群聊里为什么看不到"正在输入"？**
不是插件刻意限制，而是协议端的 `set_input_status` 本身只实现了私聊（C2C）。群聊要显示输入状态需要协议端支持，目前 NapCat 没有。

**为什么设了延迟上限？**
正常聊天不会出现需要停 20 秒的情况，上限是为了防止异常长的正文（比如误触发了长回复）把消息卡住。

## License

[MIT](LICENSE)
