"""真人节奏 —— 让 Bot 不再秒回、不再逢消息必答。

两项独立行为，各自可开关：

1. 已读不回 —— 收到纯图片消息（表情包）时按概率直接终止事件，不调用模型也不回复。
   只对不含文字的消息生效；图片配文字的照常回复。
2. 打字延迟 —— 回复发出前按正文长度计算随机停顿，避免秒回。

事件终止用 ``event.stop_event()``；停顿用 ``asyncio.sleep``，不阻塞事件循环。
已读不回的处理器优先级刻意设得很高（尽早终止，省下一次模型调用），
打字延迟则排在最后（等其它插件改写完消息链再统计长度）。
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

# 默认值，与 _conf_schema.json 保持一致
_DEFAULTS = {
    "silence": {"enable": True, "probability": 45, "cooldown_seconds": 90},
    "delay": {
        "enable": True,
        "base_seconds": 1.0,
        "per_char_seconds": 0.05,
        "jitter_seconds": 0.8,
        "max_seconds": 6.0,
    },
}


def _subscribed_message_types() -> EventMessageType:
    """同时订阅私聊与群聊。

    位运算在枚举不支持时抛 TypeError，此时退回私聊。
    """
    try:
        return EventMessageType.PRIVATE_MESSAGE | EventMessageType.GROUP_MESSAGE
    except TypeError:
        return EventMessageType.PRIVATE_MESSAGE


@register("humanlike_pace", "sijin14514-sys", "真人节奏：已读不回与打字延迟", "1.0.0")
class HumanlikePace(Star):
    """控制是否回复，以及回复前停顿多久。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self._last_silence_at = 0.0

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

    # -------------------------------------------------------------- 打字延迟

    @filter.on_decorating_result(priority=-100)
    async def delay_before_send(self, event: AstrMessageEvent) -> None:
        """按正文长度在发送前停顿。"""
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
        await asyncio.sleep(seconds)
