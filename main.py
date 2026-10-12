"""真人节奏 —— 让 Bot 不再秒回、不再逢消息必答、不再打断你打字。

四项独立行为，各自可开关：

1. 消息防抖 —— 同一会话连续几条消息合并成一条再处理，避免你还在打字她就回复了。
2. 已读不回 —— 收到纯图片消息（表情包）时按概率直接终止事件，不调用模型也不回复。
   只对不含文字的消息生效；图片配文字的照常回复。
3. 打字延迟 —— 回复发出前按正文长度计算随机停顿，避免秒回。
4. 正在输入 —— 停顿期间向平台发送「正在输入」状态，让等待可见。

事件终止用 ``event.stop_event()``；等待用 ``asyncio.sleep``，不阻塞事件循环。
"""

import asyncio
import random
import re
import time

from astrbot.api.all import AstrMessageEvent, Context, Image, Plain, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType
from astrbot.core import logger

# 「表情包管理器」插件用的发送标记，不计入打字长度
_MEME_TAG = re.compile(r"&&[^&\r\n]{1,100}&&")

# 统计长度时的上限，防止超长正文把延迟算到离谱
_MAX_COUNTED_CHARS = 400

# OneBot 扩展动作 set_input_status 的取值
_INPUT_START = 1
_INPUT_STOP = 2

# 默认值，与 _conf_schema.json 保持一致
_DEFAULTS = {
    "debounce": {"enable": True, "listen_seconds": 2.5},
    "silence": {"enable": True, "probability": 45, "cooldown_seconds": 90},
    "delay": {
        "enable": True,
        "base_seconds": 1.0,
        "per_char_seconds": 0.05,
        "jitter_seconds": 0.8,
        "max_seconds": 6.0,
    },
    "typing": {"enable": True, "min_seconds": 0.6},
}


def _subscribed_message_types() -> EventMessageType:
    """同时订阅私聊与群聊。

    位运算在枚举不支持时抛 TypeError，此时退回私聊。
    """
    try:
        return EventMessageType.PRIVATE_MESSAGE | EventMessageType.GROUP_MESSAGE
    except TypeError:
        return EventMessageType.PRIVATE_MESSAGE


@register(
    "humanlike_pace",
    "sijin14514-sys",
    "真人节奏：消息防抖、已读不回、打字延迟与正在输入",
    "1.2.0",
)
class HumanlikePace(Star):
    """控制什么时候回复、回复前停顿多久，以及怎样不打断你打字。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self._last_silence_at = 0.0
        self._typing_warned = False
        # 防抖：会话 -> 最新事件 / 待合并批次
        self._latest_event: dict[str, AstrMessageEvent] = {}
        self._batch: dict[str, list[AstrMessageEvent]] = {}

    # ------------------------------------------------------------------ 配置

    def _section(self, name: str) -> dict:
        """读取一个配置段，类型不对时返回空字典。"""
        value = self.config.get(name)
        return value if isinstance(value, dict) else {}

    def _value(self, section: str, key: str):
        """按「配置段 -> 键 -> 内置默认值」的顺序取值。"""
        section_cfg = self._section(section)
        if key in section_cfg:
            return section_cfg[key]
        return _DEFAULTS.get(section, {}).get(key)

    def _number(self, section: str, key: str) -> float:
        """取一个数值配置；非法值回落到默认值。"""
        try:
            return float(self._value(section, key))
        except (TypeError, ValueError):
            return float(_DEFAULTS[section][key])

    def _flag(self, section: str, key: str) -> bool:
        """取一个布尔配置。"""
        return bool(self._value(section, key))

    # -------------------------------------------------------------- 消息防抖

    @filter.event_message_type(_subscribed_message_types(), priority=200)
    async def debounce_messages(self, event: AstrMessageEvent) -> None:
        """把同一会话连续到达的几条消息合并成一条再交给后续流程。

        最新的一条负责等待、合并与继续处理；等待期间被合并的旧事件在此处终止，
        因此不会各自触发一次模型调用。
        """
        if not self._flag("debounce", "enable"):
            return
        window = self._number("debounce", "listen_seconds")
        if window <= 0:
            return

        umo = event.unified_msg_origin
        self._latest_event[umo] = event
        self._batch.setdefault(umo, []).append(event)

        await asyncio.sleep(window)

        # 等待期间又有新消息 => 本条让位，由更新的那条统一处理
        if self._latest_event.get(umo) is not event:
            event.stop_event()
            return

        batch = self._batch.pop(umo, [])
        self._latest_event.pop(umo, None)
        if len(batch) <= 1:
            return

        self._merge_events(event, batch)
        logger.info(
            "[humanlike_pace] 已合并 %s 条连续消息（%s）",
            len(batch), umo,
        )

    @staticmethod
    def _merge_events(target: AstrMessageEvent, batch: list[AstrMessageEvent]) -> None:
        """把批次里的消息文本与消息链合并进 target。"""
        texts: list[str] = []
        chain: list = []
        for item in batch:
            text = str(getattr(item, "message_str", "") or "").strip()
            if text:
                texts.append(text)
            components = list(getattr(item.message_obj, "message", None) or [])
            if components and chain:
                try:
                    chain.append(Plain("\n"))
                except Exception:
                    pass
            chain.extend(components)

        merged = "\n".join(texts)
        target.message_str = merged
        try:
            target.message_obj.message_str = merged
        except Exception:
            pass
        if chain:
            target.message_obj.message = chain

    # -------------------------------------------------------------- 已读不回

    @filter.event_message_type(_subscribed_message_types(), priority=100)
    async def maybe_stay_silent(self, event: AstrMessageEvent) -> None:
        """纯图片消息按概率不回复。"""
        if not self._flag("silence", "enable"):
            return
        if not self._is_image_only(event):
            return

        probability = self._number("silence", "probability")
        if probability <= 0:
            return

        cooldown = self._number("silence", "cooldown_seconds")
        now = time.monotonic()
        if cooldown > 0 and now - self._last_silence_at < cooldown:
            return

        if random.uniform(0, 100) >= probability:
            return

        self._last_silence_at = now
        event.stop_event()
        logger.info(
            "[humanlike_pace] 已读不回 %s（触发概率 %.0f%%）",
            event.unified_msg_origin,
            probability,
        )

    @staticmethod
    def _is_image_only(event: AstrMessageEvent) -> bool:
        """判断消息是否只含图片：至少一张图，且没有任何非空文字。"""
        components = getattr(event.message_obj, "message", None) or []
        has_image = False
        for component in components:
            if isinstance(component, Image):
                has_image = True
            elif isinstance(component, Plain):
                if str(getattr(component, "text", "") or "").strip():
                    return False
        return has_image

    # -------------------------------------------------------- 正在输入与延迟

    async def _set_typing(self, event: AstrMessageEvent, typing: bool) -> None:
        """向平台发送「正在输入」状态。

        走 OneBot v11 的扩展动作 ``set_input_status``（NapCat 等协议端实现）。
        该动作只支持私聊。平台不实现时静默跳过，不影响回复本身。
        """
        if not self._flag("typing", "enable"):
            return
        try:
            if not event.is_private_chat():
                return
        except Exception:
            return

        bot = getattr(event, "bot", None)
        call_action = getattr(bot, "call_action", None)
        if call_action is None:
            return

        try:
            await call_action(
                "set_input_status",
                user_id=str(event.get_sender_id()),
                event_type=_INPUT_START if typing else _INPUT_STOP,
            )
        except Exception as exc:
            # 只在首次失败时提示一次，避免刷屏
            if not self._typing_warned:
                self._typing_warned = True
                logger.info(
                    "[humanlike_pace] 平台不支持设置输入状态，已跳过：%s", exc,
                )

    @filter.on_decorating_result(priority=-100)
    async def delay_before_send(self, event: AstrMessageEvent) -> None:
        """按正文长度在发送前停顿，停顿期间显示正在输入。"""
        if not self._flag("delay", "enable"):
            return

        result = event.get_result()
        if result is None or not result.chain:
            return

        length = 0
        for component in result.chain:
            if isinstance(component, Plain):
                text = _MEME_TAG.sub("", str(getattr(component, "text", "") or ""))
                length += len(text.strip())

        # 纯图片回复没有正文，也给一点停顿，避免同步发出
        counted = min(length, _MAX_COUNTED_CHARS) if length > 0 else 1

        seconds = self._number("delay", "base_seconds")
        seconds += counted * self._number("delay", "per_char_seconds")
        jitter = max(self._number("delay", "jitter_seconds"), 0.0)
        if jitter:
            seconds += random.uniform(0, jitter)
        seconds = max(0.0, min(seconds, self._number("delay", "max_seconds")))

        if seconds <= 0:
            return

        logger.debug("[humanlike_pace] 停顿 %.2f 秒（正文 %d 字）", seconds, length)

        show_typing = seconds >= self._number("typing", "min_seconds")
        if show_typing:
            await self._set_typing(event, True)
        try:
            await asyncio.sleep(seconds)
        finally:
            if show_typing:
                await self._set_typing(event, False)
