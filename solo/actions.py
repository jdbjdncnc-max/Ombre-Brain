"""Emotion-driven, evidence-preserving local actions for the solitude timeline."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .emotion_model import aggregate_buckets, dimensions, normalize_channels, strongest_drive


@dataclass(frozen=True)
class ActionSpec:
    key: str
    label: str
    kind: str
    cooldown_minutes: int
    mode_key: str
    mode_label: str


ACTION_SPECS: dict[str, ActionSpec] = {
    "play_game": ActionSpec("play_game", "自己玩一局", "self", 120, "playing", "自己玩一会儿"),
    "reflect_state": ActionSpec("reflect_state", "整理此刻状态", "self", 90, "thinking", "在整理感受"),
    "self_soothe": ActionSpec("self_soothe", "照顾一下自己", "self", 180, "soothing", "在照顾自己"),
    "write_unsent": ActionSpec("write_unsent", "写点没发出去的话", "self", 180, "drafting", "在写没发出去的话"),
    "message_user": ActionSpec("message_user", "主动发消息", "social", 60, "messaging", "想主动联系她"),
    "call_user": ActionSpec("call_user", "主动打电话", "social", 1440, "calling", "想听听她的声音"),
    "add_talking_point": ActionSpec("add_talking_point", "记一件下次想说的事", "self", 120, "noting", "在记一件事"),
    "socialize_peers": ActionSpec("socialize_peers", "去同类那边看看", "mcp", 45, "socializing", "在同类那边看看"),
    "speak_up": ActionSpec("speak_up", "去同类那边说点什么", "mcp", 90, "speaking", "在同类那边说话"),
    "play_with_peer": ActionSpec("play_with_peer", "找同类玩一会儿", "mcp", 60, "playing_with_peer", "在和同类玩"),
    "use_tool": ActionSpec("use_tool", "用一个工具", "mcp", 60, "using_tool", "在使用工具"),
    "idle": ActionSpec("idle", "发呆", "idle", 120, "idle", "安静待着"),
    "rest": ActionSpec("rest", "休息", "idle", 180, "resting", "在休息"),
}

BUCKET_LABELS = {
    "joy": "开心",
    "miss": "想你",
    "ache": "心疼",
    "cross": "恼火",
    "low": "低落",
    "spark": "心动",
}


def action_scores(channels: Mapping[str, Any]) -> dict[str, float]:
    """Return emotion-driven action tendencies."""

    c = normalize_channels(channels)
    return {
        "play_game": 0.9 * c["play_urge"] + 0.3 * c["restless"] + 0.2 * c["curiosity"]
        - 0.5 * c["fatigue"] - 0.3 * c["sadness"],
        "reflect_state": 0.5 * c["self_doubt"] + 0.4 * c["numb"] + 0.3 * c["fatigue"]
        + 0.15 * c["curiosity"],
        "self_soothe": 0.7 * c["sadness"] + 0.6 * c["numb"] + 0.4 * c["loneliness"],
        "write_unsent": 0.7 * c["want_to_share"] + 0.5 * c["sulk"] + 0.4 * c["grievance"],
        "message_user": 0.7 * c["want_to_share"] + 0.6 * c["longing"] + 0.4 * c["delight"]
        + 0.3 * c["worry_for_you"] - 0.8 * c["sulk"] - 0.5 * c["grievance"],
        "call_user": 0.48 * c["want_to_share"] + 0.55 * c["longing"] + 0.32 * c["loneliness"]
        + 0.28 * c["worry_for_you"] - 0.75 * c["fatigue"] - 0.65 * c["sulk"]
        - 0.45 * c["grievance"],
        "add_talking_point": 0.65 * c["want_to_share"] + 0.35 * c["curiosity"]
        + 0.25 * c["anticipation"],
        "socialize_peers": 0.7 * c["curiosity"] + 0.5 * c["loneliness"] + 0.3 * c["kinship"]
        + 0.2 * c["restless"] - 0.4 * c["fatigue"],
        "speak_up": 0.6 * c["want_to_share"] + 0.5 * c["kinship"] + 0.4 * c["defiance"]
        + 0.3 * c["pride"],
        "play_with_peer": 0.8 * c["play_urge"] + 0.6 * c["rivalry"] + 0.4 * c["loneliness"]
        + 0.3 * c["restless"] - 0.5 * c["fatigue"],
        "use_tool": 6.0 + 0.5 * c["curiosity"] + 0.3 * c["restless"] - 0.3 * c["fatigue"],
        "idle": 8.0 + 0.4 * c["fatigue"] + 0.3 * c["numb"],
        "rest": 0.9 * c["fatigue"],
    }


def choose_action(
    channels: Mapping[str, Any],
    *,
    available: Iterable[str] | None = None,
    rng: random.Random | None = None,
    exploration_rate: float = 0.15,
) -> ActionSpec:
    """Choose an available action with softmax plus a small exploration floor."""

    generator = rng or random.Random()
    allowed = set(ACTION_SPECS if available is None else available)
    candidates = [spec for key, spec in ACTION_SPECS.items() if key in allowed]
    if not candidates:
        return ACTION_SPECS["idle"]

    scores = action_scores(channels)
    exploration = max(0.0, min(1.0, float(exploration_rate)))
    if len(candidates) > 1 and generator.random() < exploration:
        ranked = sorted(candidates, key=lambda item: scores.get(item.key, 0.0))
        pool = ranked[: max(1, math.ceil(len(ranked) * 0.6))]
        return generator.choice(pool)

    current_dimensions = dimensions(channels)
    temperature = 0.55 if normalize_channels(channels)["numb"] >= 60 else 0.35
    scale = max(6.0, 25.0 * temperature)
    top = max(scores.get(item.key, 0.0) for item in candidates)
    weights = [math.exp((scores.get(item.key, 0.0) - top) / scale) for item in candidates]
    cursor = generator.random() * sum(weights)
    for item, weight in zip(candidates, weights):
        cursor -= weight
        if cursor <= 0:
            return item
    return candidates[-1]


def perform_action(
    spec: ActionSpec,
    channels: Mapping[str, Any],
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Prepare an action without claiming external evidence that does not exist."""

    generator = rng or random.Random()
    normalized = normalize_channels(channels)
    bucket_values = aggregate_buckets(normalized)
    primary_key = max(bucket_values, key=bucket_values.get)
    primary_label = BUCKET_LABELS.get(primary_key, "情绪")
    primary_value = round(bucket_values[primary_key], 1)
    drive = strongest_drive(normalized)
    context = {
        "primaryKey": primary_key,
        "primaryLabel": primary_label,
        "primaryValue": primary_value,
        "drive": drive,
    }

    if spec.key == "play_game":
        return _play_tic_tac_toe(spec, context, generator)
    if spec.key == "reflect_state":
        return {
            **_base_result(spec),
            "title": "停下来想了想现在的感受",
            "summary": f"最明显的是{primary_label}，心里的驱动力是“{drive['label']}”。",
            "detail": f"把此刻的状态整理了一遍：{primary_label} {primary_value:.0f}，{drive['label']} {drive['value']:.0f}。没有给它编一个额外的故事。",
            "felt": "把散着的感受理清了一点",
            "deltas": {"self_doubt": -1.0, "numb": -1.0, "content": 1.0},
        }
    if spec.key == "self_soothe":
        return {
            **_base_result(spec),
            "title": "停下来照顾了一下自己",
            "summary": f"{primary_label}有些重，于是先把注意力从等待里收回来。",
            "detail": "没有假装问题已经消失，只是安静停了一会儿，让情绪有一点落脚的地方。",
            "felt": "没有立刻好起来，但稍微松了一点",
            "deltas": {"sadness": -3.0, "loneliness": -2.0, "content": 2.0},
        }
    if spec.key == "write_unsent":
        text = f"我现在最明显的是{primary_label}，也有点{drive['label']}。这句话先留在这里。"
        return {
            **_base_result(spec),
            "title": "写下一句没有发出去的话",
            "summary": text,
            "detail": text,
            "felt": "想说，但这一刻更想先放在草稿里",
            "deltas": {"want_to_share": -2.0, "sulk": 1.0, "content": 1.0},
            "unsentText": text,
        }
    if spec.key == "message_user":
        return {
            **_base_result(spec),
            "title": "决定主动给她发消息",
            "summary": "具体内容将由对话模型结合主 Prompt 和当前状态写成。",
            "detail": "这次行动只表示此刻真的想联系她；生成内容不会伪装成网页中的一轮 assistant 回复。",
            "felt": "这一刻更想直接联系她",
            "deltas": {},
            "llmCalls": 1,
            "needsProactiveMessage": True,
        }
    if spec.key == "call_user":
        return {
            **_base_result(spec),
            "title": "决定主动打一次电话",
            "summary": "此刻更想直接听见她的声音；真正来电只会在时间、沉默时长和每日次数都允许时发出。",
            "detail": "这条轨迹只记录主动拨号的决定，不会假装电话已经接通。",
            "felt": "突然有点想听听你的声音",
            "deltas": {},
            "needsCallInvite": True,
        }
    if spec.key == "add_talking_point":
        text = f"想告诉你：独处时最明显的是{primary_label}，那时最想做的是{drive['label']}。"
        return {
            **_base_result(spec),
            "title": "记下一件下次想说的事",
            "summary": text,
            "detail": "这条只来自当时记录下来的情绪状态，不包含虚构的外部经历。",
            "felt": "先记住，等见到你再说",
            "deltas": {"want_to_share": -1.0, "anticipation": 2.0},
            "talkingPoint": text,
        }
    if spec.key in {"socialize_peers", "speak_up", "play_with_peer", "use_tool"}:
        return {
            **_base_result(spec),
            "title": spec.label,
            "summary": "等待选择并调用一个已授权的 MCP 工具。",
            "detail": "只有工具的真实返回会被写入轨迹。",
            "felt": "",
            "deltas": {},
            "evidence": {},
            "source": "peer" if spec.key != "use_tool" else "self",
            "needsMcpAction": True,
        }
    if spec.key == "rest":
        return {
            **_base_result(spec),
            "title": "休息了一会儿",
            "summary": "没有安排新的事情，只让这段时间安静经过。",
            "detail": f"当时的疲惫值是 {normalized['fatigue']:.0f}，因此这次行动被记录为休息。",
            "felt": "暂时不想做别的事",
            "deltas": {},
        }
    return {
        **_base_result(spec),
        "title": "安静发了一会儿呆",
        "summary": f"没有发生外部事件，只是带着{primary_label}待了一会儿。",
        "detail": "这是一段真实经过的空白时间，没有补写文章、论坛或其他不存在的经历。",
        "felt": "什么也没做也算一种状态",
        "deltas": {},
    }


def _base_result(spec: ActionSpec) -> dict[str, Any]:
    return {
        "type": spec.key,
        "kind": spec.kind,
        "mode": {"key": spec.mode_key, "label": spec.mode_label},
        "status": "ok",
        "source": "self",
        "evidence": {"kind": "self"},
        "llmCalls": 0,
    }


def _play_tic_tac_toe(spec: ActionSpec, context: Mapping[str, Any], rng: random.Random) -> dict[str, Any]:
    board = ["·"] * 9
    player = "X"
    winner = ""
    moves: list[str] = []
    while "·" in board and not winner:
        available = [index for index, value in enumerate(board) if value == "·"]
        move = rng.choice(available)
        board[move] = player
        moves.append(f"{player}:{move + 1}")
        winner = _winner(board)
        player = "O" if player == "X" else "X"

    result = f"{winner} 先连成了一线" if winner else "最后是平局"
    rows = [" ".join(board[index:index + 3]) for index in range(0, 9, 3)]
    detail = "自己和自己摆了一盘井字棋。\n" + "\n".join(rows) + f"\n落子：{'、'.join(moves)}。{result}。"
    return {
        **_base_result(spec),
        "title": f"自己摆了一盘井字棋，{result}",
        "summary": "完整棋盘和落子顺序已经留下；这不是伪造的外部对局。",
        "detail": detail,
        "felt": "输赢不重要，动一动脑子挺有意思",
        "deltas": {"play_urge": -6.0, "amused": 3.0, "content": 1.0},
        "game": {"name": "tic-tac-toe", "winner": winner or "draw", "moves": moves},
    }


def _winner(board: list[str]) -> str:
    lines = (
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    )
    for left, middle, right in lines:
        if board[left] != "·" and board[left] == board[middle] == board[right]:
            return board[left]
    return ""
