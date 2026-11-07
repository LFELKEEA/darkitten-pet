"""右键「动作」菜单：用深度求索娘自己的动作和表情替换小猫菜单。"""

from __future__ import annotations

CLIP_LABELS = {
    "wave": "挥手",
    "greet": "打招呼",
    "nod": "点头",
    "shake": "摇头",
    "tilt": "歪头",
    "hop": "蹦一下",
    "stretch": "伸懒腰",
    "listen": "听你说",
    "shy": "害羞",
    "love": "比心",
    "pout": "鼓脸",
    "squint": "＞＜",
    "surprise": "惊讶",
    "dizzy": "圈圈眼",
    "cry": "流泪",
    "deadpan": "黑脸",
    "sweat": "流冷汗",
    "proud": "得意",
    "sigh": "叹气",
    "sing": "唱歌",
    "dance": "转圈跳舞",
    "think": "思考",
    "glance": "东张西望",
    "bow": "鞠躬",
    "sneeze": "打喷嚏",
    "wave_big": "用力挥手",
    "dress": "换长裙",
    "chibi": "变小",
    "tail_hide": "藏尾巴",
    "tail_show": "露尾巴",
    "hand_up": "举手",
    "hand_down": "放下手",
    "foxtail_out": "拿出狗尾草",
    "foxtail_in": "收起狗尾草",
    "point_out": "竖起手指",
    "point_in": "收回手指",
    "special": "隐藏动作",
    "idle_motion": "待机动作",
}

EXPRESSION_LABELS = {
    "blush": "脸红",
    "heart": "爱心眼",
    "smug": "屑屑眼",
    "squint": "＞＜",
    "dizzy": "圈圈眼",
    "tears": "流泪",
    "dark": "黑脸",
    "sweat": "流冷汗",
    "fang": "兔牙",
    "loading": "加载中",
    "pot": "钢盆扣头",
    "glasses": "黑框眼镜",
    "monocle": "单片眼镜",
    "body_default": "默认体型",
    "body_small": "体型·小",
    "body_large": "体型·大",
    "lean": "前倾",
    "chest_cloth": "胸口衣服",
    "shoes_off": "脱鞋",
    "tail_off": "隐藏尾巴",
    "point_on": "竖起食指",
    "point_off": "收回食指",
}

GROUPS = (
    ("常用", ("wave", "greet", "nod", "shake", "tilt", "hop", "stretch", "listen")),
    (
        "情绪",
        ("shy", "love", "pout", "squint", "surprise", "dizzy", "cry", "deadpan",
         "sweat", "proud", "sigh"),
    ),
    ("表演", ("sing", "dance", "think", "glance", "bow", "sneeze", "wave_big")),
    (
        "换装与小道具",
        ("dress", "chibi", "tail_hide", "tail_show", "hand_up", "hand_down",
         "foxtail_out", "foxtail_in", "point_out", "point_in", "special", "idle_motion"),
    ),
)


def build_menu(clips, expressions, play, expression, clear_expression) -> list[dict]:
    """生成 set_action_menu 需要的分组；只包含模型实际有的动作。"""
    clip_names = set(clips or {})
    expression_names = set(expressions or {})
    groups: list[dict] = []

    for title, names in GROUPS:
        items = [
            {"label": CLIP_LABELS[name], "callback": (lambda name=name: play(name))}
            for name in names
            if name in clip_names
        ]
        if items:
            groups.append({"title": title, "items": items})

    expr_items = [
        {"label": EXPRESSION_LABELS[name], "callback": (lambda name=name: expression(name))}
        for name in EXPRESSION_LABELS
        if name in expression_names
    ]
    if expr_items:
        expr_items.append({"label": "恢复默认表情", "callback": clear_expression})
        groups.append({"title": "表情", "items": expr_items})

    return groups
