"""照护编排：摄入事件 → 按当时有效规则分层 → 只追加结论，并生成
与个人风险相符的提醒和中文病程回顾。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .records import (
    EVENT_FACT_FIELDS,
    CareTimeline,
    Conclusion,
    Event,
)
from .rules import LEVEL_NAMES_ZH, RiskInputs, RuleBook

LEVEL_ORDER = ["lifestyle", "review", "medical", "urgent"]


def ingest_event(
    timeline: CareTimeline,
    rulebook: RuleBook,
    day: date,
    event_type: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    source: str = "",
    note: str = "",
) -> tuple[Event, Conclusion | None]:
    """追加一条事件；若该事件携带分层事实，则按当日有效规则追加新结论。

    旧结论永不修改：新结论通过 supersedes 指向上一条结论，表示“接替”而非“覆盖”。
    """
    event = timeline.append_event(
        day=day,
        event_type=event_type,
        actor=actor,
        payload=payload,
        source=source,
    )
    if event_type not in EVENT_FACT_FIELDS:
        return event, None

    snapshot = timeline.fact_snapshot(day)
    stratification = rulebook.stratify(RiskInputs(as_of=day, values=snapshot))
    prior = timeline.latest_conclusion()
    conclusion = timeline.append_conclusion(
        day=day,
        event_ref=event.seq,
        stratification=stratification.as_dict(),
        note=note,
        supersedes=prior.seq if prior else None,
    )
    return event, conclusion


# ---------------------------------------------------------------------------
# 差异化患者提醒：按本人最新层级与具体事实生成，而非全员同一条消息。
# ---------------------------------------------------------------------------

_REVIEW_WINDOW_DAYS = {
    "lifestyle": 180,
    "review": 28,
    "medical": 7,
    "urgent": 3,
}


@dataclass(frozen=True)
class Reminder:
    priority: str        # urgent / high / routine
    title: str
    detail: str


def patient_reminders(timeline: CareTimeline, today: date) -> list[Reminder]:
    latest = timeline.latest_conclusion()
    if latest is None:
        return [
            Reminder("routine", "完成干眼筛查", "尚未建立分层结论，请先完成问卷与基础检查。")
        ]

    facts = timeline.fact_snapshot(latest.date)
    level = latest.level
    reminders: list[Reminder] = [
        Reminder(
            "urgent" if level == "urgent" else "high" if level == "medical" else "routine",
            f"当前风险层级：{LEVEL_NAMES_ZH[level]}",
            f"该结论于 {latest.date.isoformat()} 按当时有效规则（v{latest.rule_version}）作出，依据："
            + "；".join(latest.reasons),
        )
    ]

    window = _REVIEW_WINDOW_DAYS[level]
    due = latest.date + timedelta(days=window)
    reminders.append(
        Reminder(
            "urgent" if level == "urgent" else "high" if level == "medical" else "routine",
            {
                "urgent": f"请在 {due.isoformat()} 前就诊（必要时当天）",
                "medical": f"请在 {due.isoformat()} 前到眼科门诊",
                "review": f"请在 {due.isoformat()} 前完成复查",
                "lifestyle": f"下次常规复查约在 {due.isoformat()}",
            }[level],
            "携带症状记录与正在使用的全部眼药水清单。" if level in ("medical", "urgent") else "",
        )
    )

    # 停止自助用药的硬性提醒，按具体药物与层级区分。
    if facts.get("steroid_drops_without_prescription"):
        reminders.append(
            Reminder(
                "urgent",
                "立即停用自行购买的含激素眼药水",
                "含激素滴眼液可能升高眼压、掩盖感染。请立即停用并尽快就诊，就诊时携带药瓶。",
            )
        )
    if level == "urgent":
        reminders.append(
            Reminder(
                "urgent",
                "立即停止一切自行滴药",
                "在当前紧急风险下继续自助用药可能延误角膜损伤或感染的处理。",
            )
        )
    elif facts.get("vasoconstrictor_drops"):
        reminders.append(
            Reminder(
                "high",
                "去红血丝类缩血管眼药水需在医生指导下停用",
                "长期使用会反跳性加重眼红，请勿突然长期依赖，也不要换个品牌继续滴。",
            )
        )

    if facts.get("planning_refractive_surgery"):
        reminders.append(
            Reminder(
                "high",
                "近视手术前先完成眼表评估",
                "泪膜未稳定时手术可能加重干眼；请先在眼科完成术前泪膜评估再定手术日期。",
            )
        )

    if facts.get("screen_hours"):
        threshold = 6 if latest.rule_version >= 2 else 8
        if facts["screen_hours"] >= threshold:
            reminders.append(
                Reminder(
                    "routine",
                    f"每日盯屏约 {facts['screen_hours']} 小时",
                    "每20分钟看20英尺外20秒，刻意完整眨眼；屏幕上缘放低至视线以下，避开空调直吹。",
                )
            )

    if level == "lifestyle" and not facts.get("self_medication"):
        reminders.append(
            Reminder(
                "routine",
                "无需特殊用药",
                "眼干时优先环境与休息调整，必要时使用无防腐剂人工泪液即可。",
            )
        )

    return reminders


# ---------------------------------------------------------------------------
# 医护病程回顾：升级原因、处理经过、必须停止自助用药的时点。
# ---------------------------------------------------------------------------

def _actions_taken(timeline: CareTimeline) -> list[str]:
    """按时间列出实际处理经过。

    生活干预/复查/转诊来自照护事件；医嘱建议来自结论，但同一条建议在
    连续多条同层级结论中重复出现时，只在“首次下达”的日期记录一次。
    """
    taken: list[str] = []
    for event in timeline.events:
        if event.event_type == "lifestyle_advice":
            taken.append(
                f"{event.date.isoformat()} 生活干预："
                f"{event.payload.get('detail', '').strip() or '用眼与环境调整'}"
            )
        elif event.event_type == "follow_up":
            taken.append(
                f"{event.date.isoformat()} 复查："
                f"{event.payload.get('result', '已安排/完成复查')}"
            )
        elif event.event_type == "referral":
            taken.append(
                f"{event.date.isoformat()} 转诊至{event.payload.get('to', '眼科')}"
                f"（{event.payload.get('reason', '')}）"
            )
    first_seen: dict[str, str] = {}
    for c in timeline.conclusions:
        for action in c.actions:
            first_seen.setdefault(
                action, c.date.isoformat()
            )  # 结论按时间追加，首次即最早
    for action, day in first_seen.items():
        taken.append(f"{day} 医嘱建议：{action}")
    return sorted(set(taken))


def self_medication_stop_directives(timeline: CareTimeline) -> list[dict[str, str]]:
    """列出“何时必须停止自助用药”的明确节点。"""
    directives: list[dict[str, str]] = []
    for c in timeline.conclusions:
        facts = timeline.fact_snapshot(c.date)
        if facts.get("steroid_drops_without_prescription"):
            directives.append(
                {
                    "date": c.date.isoformat(),
                    "level": LEVEL_NAMES_ZH[c.level],
                    "directive": "立即停用自行购买的含激素眼药水并就诊",
                    "basis": "结论 v{} 命中：{}".format(
                        c.rule_version, "；".join(c.reasons)
                    ),
                }
            )
        elif c.level == "urgent":
            directives.append(
                {
                    "date": c.date.isoformat(),
                    "level": LEVEL_NAMES_ZH[c.level],
                    "directive": "立即停止一切自行滴药，按急诊医嘱处理",
                    "basis": "；".join(c.reasons),
                }
            )
        elif c.level == "medical" and facts.get("vasoconstrictor_drops"):
            directives.append(
                {
                    "date": c.date.isoformat(),
                    "level": LEVEL_NAMES_ZH[c.level],
                    "directive": "缩血管眼药水在医生指导下停用，停止自助换药",
                    "basis": "；".join(c.reasons),
                }
            )
    # 同一条停用指令可能在连续多条结论中重复出现，只保留最早提出的节点。
    earliest: dict[str, dict[str, str]] = {}
    for d in directives:
        prev = earliest.get(d["directive"])
        if prev is None or d["date"] < prev["date"]:
            earliest[d["directive"]] = d
    return [earliest[d] for d in sorted(earliest, key=lambda k: earliest[k]["date"])]


def review_narrative(timeline: CareTimeline) -> dict[str, Any]:
    """生成给医护看的结构化病程回顾。"""
    conclusions = timeline.conclusions
    latest = conclusions[-1] if conclusions else None
    escalations = [
        {
            "date": c.date.isoformat(),
            "from_rule_version": None,  # 见下方填充
            "to_level": LEVEL_NAMES_ZH[c.level],
            "matched_rule": c.matched_rule_name,
            "reasons": c.reasons,
            "rule_version": c.rule_version,
        }
        for c in timeline.escalation_history()
    ]
    for idx, item in enumerate(escalations):
        item["from_rule_version"] = (
            escalations[idx - 1]["rule_version"] if idx > 0 else None
        )

    return {
        "person_id": timeline.person_id,
        "latest": (
            {
                "date": latest.date.isoformat(),
                "level": LEVEL_NAMES_ZH[latest.level],
                "rule_version": latest.rule_version,
                "reasons": latest.reasons,
                "actions": latest.actions,
            }
            if latest
            else None
        ),
        "escalations": escalations,
        "actions_taken": _actions_taken(timeline),
        "stop_self_medication": self_medication_stop_directives(timeline),
        "conclusion_chain": [
            {
                "seq": c.seq,
                "date": c.date.isoformat(),
                "level": LEVEL_NAMES_ZH[c.level],
                "rule_version": c.rule_version,
                "supersedes": c.supersedes,
                "immutable": True,
            }
            for c in conclusions
        ],
    }
