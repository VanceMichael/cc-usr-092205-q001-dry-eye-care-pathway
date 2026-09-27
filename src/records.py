"""只追加（append-only）的连续照护时间线。

每一条事件都携带前一条事件的哈希，形成哈希链：
- 患者补报症状、药物变化、术前诉求时只追加新事件与新结论；
- 旧事件与旧结论不可修改、不可删除，篡改会破坏哈希链校验；
- 新评估需要历史事实时，是在“重放全部历史事实”的快照上完成，
  而不是改写旧结论。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# 各事件类型携带的事实字段（用于校验与说明）。
EVENT_FACT_FIELDS: dict[str, tuple[str, ...]] = {
    "screening_questionnaire": (
        "osdi",
        "symptom_duration_days",
        "foreign_body_sensation",
        "dryness",
        "vision_fluctuation",
    ),
    "work_environment": (
        "screen_hours",
        "ac_direct_blast",
        "humidity_low",
        "screen_above_eye_level",
    ),
    "blink_habits": (
        "reduced_blink_rate",
        "incomplete_blink",
        "contact_lens_wear",
        "contact_lens_discomfort",
    ),
    "medical_history": ("systemic_risk_disease", "prior_eye_surgery"),
    "long_term_medication": ("systemic_risk_medication",),
    "tear_film_exam": (
        "but_seconds",
        "schirmer_mm",
        "corneal_staining_grade",
        "conjunctival_staining_grade",
    ),
    "doctor_assessment": ("doctor_inflammation", "doctor_corneal_risk"),
    "patient_update": (
        "symptom_duration_days",
        "foreign_body_sensation",
        "dryness",
        "redness_or_pain",
        "photophobia",
        "vision_fluctuation",
    ),
    "medication_change": (
        "self_medication",
        "vasoconstrictor_drops",
        "steroid_drops_without_prescription",
        "stopped_self_medication",
    ),
    "surgery_intention": ("planning_refractive_surgery",),
}

# 事实组：同组事件视为对该组事实的“完整新报告”，重放时整组替换，
# 例如新问卷到达后，上一份问卷中未再报告的症状视为已复查为无，
# 避免几个月前的旧症状被当成当前事实。
EVENT_GROUPS: dict[str, str] = {
    "screening_questionnaire": "symptoms",
    "patient_update": "symptoms",
    "work_environment": "environment",
    "blink_habits": "blink",
    "medical_history": "history",
    "long_term_medication": "systemic_medication",
    "tear_film_exam": "tear_film",
    "doctor_assessment": "doctor",
    "medication_change": "medication",
    "surgery_intention": "surgery",
}

# 处置/随访类事件不提供分层事实，只进入照护经过。
NON_FACT_EVENTS = {"lifestyle_advice", "follow_up", "referral", "note"}


@dataclass(frozen=True)
class Event:
    seq: int
    date: date
    event_type: str
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    prev_hash: str = ""
    event_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "date": self.date.isoformat(),
            "event_type": self.event_type,
            "payload": self.payload,
            "actor": self.actor,
            "source": self.source,
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
        }


@dataclass(frozen=True)
class Conclusion:
    """某次评估给出的分层结论，一经写入即冻结。"""

    seq: int
    date: date
    event_ref: int
    rule_version: int
    level: str
    matched_rule_id: str
    reasons: list[str]
    actions: list[str]
    matched_rule_name: str = ""
    note: str = ""
    supersedes: int | None = None  # 仅指向被新结论接替的旧结论序号，旧结论仍保留
    prev_hash: str = ""
    event_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "date": self.date.isoformat(),
            "event_ref": self.event_ref,
            "rule_version": self.rule_version,
            "level": self.level,
            "matched_rule_id": self.matched_rule_id,
            "matched_rule_name": self.matched_rule_name,
            "reasons": self.reasons,
            "actions": self.actions,
            "note": self.note,
            "supersedes": self.supersedes,
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


def digest(parts: list[Any]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(_canonical(part))
        h.update(b"|")
    return h.hexdigest()


class TimelineIntegrityError(RuntimeError):
    pass


class CareTimeline:
    """同一个人在同一统一身份下的全部照护事件与结论。"""

    def __init__(self, person_id: str):
        self.person_id = person_id
        self.events: list[Event] = []
        self.conclusions: list[Conclusion] = []

    # ---- 写入：只追加 -------------------------------------------------

    def append_event(
        self,
        day: date,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        source: str = "",
    ) -> Event:
        payload = dict(payload or {})
        known = set(EVENT_FACT_FIELDS) | NON_FACT_EVENTS
        if event_type not in known:
            raise ValueError(f"未知事件类型: {event_type}")
        seq = len(self.events) + 1
        prev = self.events[-1].event_hash if self.events else ""
        event = Event(
            seq=seq,
            date=day,
            event_type=event_type,
            actor=actor,
            payload=payload,
            source=source,
            prev_hash=prev,
        )
        h = digest(
            [
                self.person_id,
                seq,
                day.isoformat(),
                event_type,
                actor,
                payload,
                source,
                prev,
            ]
        )
        object.__setattr__(event, "event_hash", h)
        self.events.append(event)
        return event

    def append_conclusion(
        self,
        day: date,
        event_ref: int,
        stratification: dict[str, Any],
        note: str = "",
        supersedes: int | None = None,
    ) -> Conclusion:
        if not 1 <= event_ref <= len(self.events):
            raise ValueError("结论必须引用一条已存在的事件")
        if supersedes is not None and not any(
            c.seq == supersedes for c in self.conclusions
        ):
            raise ValueError("接替关系必须指向已存在的旧结论")
        seq = len(self.conclusions) + 1
        prev = self.conclusions[-1].event_hash if self.conclusions else ""
        conclusion = Conclusion(
            seq=seq,
            date=day,
            event_ref=event_ref,
            rule_version=stratification["rule_version"],
            level=stratification["level"],
            matched_rule_id=stratification["matched_rule_id"],
            matched_rule_name=stratification.get("matched_rule_name", ""),
            reasons=list(stratification["reasons"]),
            actions=list(stratification["actions"]),
            note=note,
            supersedes=supersedes,
            prev_hash=prev,
        )
        h = digest(
            [
                self.person_id,
                "conclusion",
                seq,
                day.isoformat(),
                event_ref,
                conclusion.rule_version,
                conclusion.level,
                conclusion.matched_rule_id,
                conclusion.matched_rule_name,
                conclusion.reasons,
                conclusion.actions,
                conclusion.note,
                conclusion.supersedes,
                prev,
            ]
        )
        object.__setattr__(conclusion, "event_hash", h)
        self.conclusions.append(conclusion)
        return conclusion

    # ---- 读取与重放 ---------------------------------------------------

    def fact_snapshot(self, as_of: date) -> dict[str, Any]:
        """重放截至某日的全部事实事件，得到当时的累计事实快照。

        按事实组整组替换：同组新事件（如新一份问卷、新一次泪膜检查、
        新一次用药自报）代表对该组事实的完整新报告，旧报告中未再出现的字段
        不再视为当前事实——避免几个月前的旧症状/旧检查值被当成现状。
        历史事件与旧结论本身保持不变，这里变化的只是“当前快照”。
        """
        groups: dict[str, dict[str, Any]] = {}
        for event in self.events:
            if event.date > as_of:
                break
            group = EVENT_GROUPS.get(event.event_type)
            if group is None:
                continue
            fields = EVENT_FACT_FIELDS[event.event_type]
            latest = {f: event.payload[f] for f in fields if f in event.payload}
            groups[group] = latest
        values: dict[str, Any] = {}
        for group_values in groups.values():
            values.update(group_values)
        return values

    def latest_conclusion(self) -> Conclusion | None:
        return self.conclusions[-1] if self.conclusions else None

    def escalation_history(self) -> list[Conclusion]:
        """风险层级较“上一条结论”升高的结论序列（症状为何升级）。

        与历史最高值比较会漏掉“好转后再次恶化”的升级，因此只与紧邻的上一条比。
        """
        order = {"lifestyle": 0, "review": 1, "medical": 2, "urgent": 3}
        history: list[Conclusion] = []
        prev_level: int | None = None
        for c in self.conclusions:
            if prev_level is not None and order[c.level] > prev_level:
                history.append(c)
            prev_level = order[c.level]
        return history

    def verify(self) -> None:
        """校验哈希链：序号、前向链接、内容摘要任一被改动即失败。"""
        prev = ""
        for idx, event in enumerate(self.events, start=1):
            if event.seq != idx:
                raise TimelineIntegrityError(f"事件序号断裂: {event.seq}")
            if event.prev_hash != prev:
                raise TimelineIntegrityError(f"事件 {event.seq} 前向链接不匹配")
            expected = digest(
                [
                    self.person_id,
                    event.seq,
                    event.date.isoformat(),
                    event.event_type,
                    event.actor,
                    event.payload,
                    event.source,
                    prev,
                ]
            )
            if event.event_hash != expected:
                raise TimelineIntegrityError(f"事件 {event.seq} 内容与摘要不符")
            prev = event.event_hash

        prev = ""
        for c in self.conclusions:
            if c.prev_hash != prev:
                raise TimelineIntegrityError(f"结论 {c.seq} 前向链接不匹配")
            expected = digest(
                [
                    self.person_id,
                    "conclusion",
                    c.seq,
                    c.date.isoformat(),
                    c.event_ref,
                    c.rule_version,
                    c.level,
                    c.matched_rule_id,
                    c.matched_rule_name,
                    c.reasons,
                    c.actions,
                    c.note,
                    c.supersedes,
                    prev,
                ]
            )
            if c.event_hash != expected:
                raise TimelineIntegrityError(f"结论 {c.seq} 内容与摘要不符")
            prev = c.event_hash
