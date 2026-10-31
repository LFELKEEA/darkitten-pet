from __future__ import annotations

import random
from datetime import datetime


PAT_LINES = [
    "哼，才、才不是想让你摸……",
    "再摸一下也不是不行。",
    "喵……别摸头啦，会长不高的。",
    "主人今天很闲嘛。",
    "唔……舒服。",
    "只准摸三下哦。",
]

POKE_LINES = [
    "干嘛戳我？",
    "在的，有事就说。",
    "又怎么了，笨蛋。",
    "我一直都在啦。",
    "戳坏了你负责吗？",
]

DRAG_LINES = [
    "哇，要掉下去了！",
    "轻、轻一点啦。",
    "别把我扔到屏幕外面。",
    "主人要带我去哪儿？",
]

SLEEP_LINES = [
    "那我去眯一会儿……有事叫我。",
    "晚安……Zzz。",
    "唔，终于可以休息了。",
]

WAKE_LINES = [
    "唔……谁吵醒我……",
    "还没睡够呢。",
    "嗯？主人找我？",
]

IDLE_LINES = [
    "主人在忙吗？",
    "桌面有点安静呢。",
    "要不要休息一下眼睛？",
    "我一直都在哦。",
    "哼，才没有在看你。",
    "记得喝水。",
    "坐久了就站起来走两步。",
    "今天也辛苦了。",
]

CHAT_OPEN_LINES = [
    "想聊什么？我听着。",
    "终于想起我啦？",
    "嗯，我在。",
]

FALLBACK_LINES = [
    "唔……这个我得想想。",
    "哼，说慢一点啦。",
    "我在听，继续说。",
    "主人今天话好多。",
    "这个嘛……你猜？",
    "笨蛋，直接说重点。",
]

AI_FAIL_NOTES = [
    "AI 通道暂时没连上，LFELKEEA 先用本地小脑袋陪你聊。",
    "网络那边没回应，先这样聊吧。",
]

ACTION_LINES = {
    "hop": ["跳一下！", "嘿！", "看我的。"],
    "spin": ["转个圈给你看。", "别眨眼哦。", "嘿——"],
    "stretch": ["伸个懒腰……", "坐久了，活动一下。", "唔——舒服。"],
    "shake": ["抖抖耳朵。", "别闹啦。", "嗯？"],
    "lean": ["嗯？", "在看我吗？", "怎么啦。"],
    "nod": ["嗯嗯。", "知道了。", "好。"],
}


def greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 11:
        return "早上好呀，主人。今天也要打起精神。"
    if 11 <= hour < 14:
        return "中午啦，别忘了吃饭。"
    if 14 <= hour < 18:
        return "下午好……有点困，你呢？"
    if 18 <= hour < 23:
        return "晚上好，今天辛苦了。"
    return "还不睡吗？熬夜会变笨的。"


def local_reply(text: str) -> str:
    t = text.strip().lower()
    if not t:
        return random.choice(FALLBACK_LINES)

    if any(k in t for k in ("你叫什么", "你的名字", "名字叫")):
        return "LFELKEEA。记住了，不许叫错。"
    if any(k in t for k in ("喜欢你", "爱你", "喜欢我")):
        return "哼……才、才不讨厌你。"
    if any(k in t for k in ("累", "困", "疲惫", "加班")):
        return "那就靠一会儿，我帮你看着桌面。"
    if any(k in t for k in ("晚安", "睡觉", "睡了")):
        return "晚安，主人。做个好梦。"
    if any(k in t for k in ("早安", "早上好")):
        return "早呀。今天也一起加油吧。"
    if any(k in t for k in ("你好", "在吗", "嗨", "hello", "hi")):
        return "在呢。想和我说什么？"
    if any(k in t for k in ("吃", "饿")):
        return "记得好好吃饭，别拿零食糊弄。"
    if any(k in t for k in ("难受", "难过", "不开心", "烦", "压力")):
        return "先深呼吸一下。我陪着你，慢慢说。"
    if any(k in t for k in ("可爱", "好看", "漂亮")):
        return "这、这不是很明显吗……笨蛋。"
    if any(k in t for k in ("你是ai", "你是ai吗", "机器人", "模型")):
        return "我是 LFELKEEA，住在你桌面上的 LFELKEEA。"
    if any(k in t for k in ("谢谢", "感谢")):
        return "不用谢。下次也记得找我。"
    if any(k in t for k in ("再见", "拜拜")):
        return "去忙吧。我会在这儿等你。"
    return random.choice(FALLBACK_LINES)
