"""版本化的干眼风险分层规则。

规则集以生效日期区间管理：每次评估都锁定“评估当日有效”的规则版本，
旧结论永久保留当时命中的规则版本与理由，规则更新不会追溯改写历史。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

LEVEL_ORDER = ["lifestyle", "review", "medical", "urgent"]
LEVEL_NAMES_ZH = {
    "lifestyle": "生活干预",
    "review": "安排复查",
    "medical": "眼科就医",
    "urgent": "紧急就医",
}


@dataclass(frozen=True)
class RuleSet:
    """某一时段有效的一版分层规则。"""

    version: int
    name: str
    effective_from: date
    effective_to: date | None
    levels: list[str]
    rules: list[dict[str, Any]]
    actions: dict[str, list[str]]

    def effective_on(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


@dataclass(frozen=True)
class RiskInputs:
    """一次分层所依据的事实快照（问卷/环境/习惯/病史/用药/检查/医生判断）。"""

    as_of: date
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Any:
        return self.values.get(key)


@dataclass(frozen=True)
class Stratification:
    """一次分层结论：层级、命中的规则与事实依据、对应处置建议。"""

    rule_version: int
    rule_set_name: str
    level: str
    matched_rule_id: str
    matched_rule_name: str
    reasons: list[str]
    actions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_version": self.rule_version,
            "rule_set_name": self.rule_set_name,
            "level": self.level,
            "level_name": LEVEL_NAMES_ZH[self.level],
            "matched_rule_id": self.matched_rule_id,
            "matched_rule_name": self.matched_rule_name,
            "reasons": self.reasons,
            "actions": self.actions,
        }


class RuleBook:
    """按日期选择有效规则集并执行分层。"""

    def __init__(self, rule_sets: list[RuleSet]):
        if not rule_sets:
            raise ValueError("至少需要一版规则")
        self._rule_sets = sorted(rule_sets, key=lambda rs: rs.effective_from)
        self._validate_coverage()

    def _validate_coverage(self) -> None:
        versions = [rs.version for rs in self._rule_sets]
        if len(versions) != len(set(versions)):
            raise ValueError("规则版本号不得重复")
        for rs in self._rule_sets:
            for level in rs.levels:
                if level not in LEVEL_ORDER:
                    raise ValueError(f"未知风险层级: {level}")
                if level not in rs.actions:
                    raise ValueError(f"规则 v{rs.version} 缺少层级 {level} 的处置建议")

    @classmethod
    def from_path(cls, path: str | Path) -> "RuleBook":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleBook":
        sets = [
            RuleSet(
                version=rs["version"],
                name=rs["name"],
                effective_from=date.fromisoformat(rs["effective_from"]),
                effective_to=(
                    date.fromisoformat(rs["effective_to"])
                    if rs.get("effective_to")
                    else None
                ),
                levels=list(rs["levels"]),
                rules=rs["rules"],
                actions={k: list(v) for k, v in rs["actions"].items()},
            )
            for rs in data["rule_sets"]
        ]
        return cls(sets)

    def versions(self) -> list[int]:
        return [rs.version for rs in self._rule_sets]

    def effective_set(self, day: date) -> RuleSet:
        active = [rs for rs in self._rule_sets if rs.effective_on(day)]
        if not active:
            raise LookupError(f"{day.isoformat()} 没有生效的分层规则")
        if len(active) > 1:
            raise ValueError(f"{day.isoformat()} 存在多版同时生效的规则")
        return active[0]

    def stratify(self, inputs: RiskInputs) -> Stratification:
        rule_set = self.effective_set(inputs.as_of)
        # 规则按紧急程度从高到低书写；取命中的最高层级。
        for level in reversed(LEVEL_ORDER):
            for rule in rule_set.rules:
                if rule["level"] != level:
                    continue
                reasons = _evaluate(rule, inputs)
                if reasons:
                    return Stratification(
                        rule_version=rule_set.version,
                        rule_set_name=rule_set.name,
                        level=level,
                        matched_rule_id=rule["id"],
                        matched_rule_name=rule["name"],
                        reasons=reasons,
                        actions=list(rule_set.actions[level]),
                    )
        return Stratification(
            rule_version=rule_set.version,
            rule_set_name=rule_set.name,
            level="lifestyle",
            matched_rule_id="default-lifestyle",
            matched_rule_name="常规健康促进",
            reasons=["未命中任何风险规则，属于日常用眼健康促进范围"],
            actions=list(rule_set.actions["lifestyle"]),
        )


def _evaluate(rule: dict[str, Any], inputs: RiskInputs) -> list[str]:
    """返回命中事实的中文依据；未命中返回空列表。"""
    return _evaluate_clause(rule, inputs)


def _evaluate_clause(clause: dict[str, Any], inputs: RiskInputs) -> list[str]:
    if "any" in clause:
        for sub in clause["any"]:
            reasons = _evaluate_clause(sub, inputs)
            if reasons:
                return reasons
        return []
    if "all" in clause:
        collected: list[str] = []
        for sub in clause["all"]:
            reasons = _evaluate_clause(sub, inputs)
            if not reasons:
                return []
            collected.extend(reasons)
        return collected
    return _evaluate_condition(clause, inputs)


def _evaluate_condition(cond: dict[str, Any], inputs: RiskInputs) -> list[str]:
    key, op, expected = cond["key"], cond["op"], cond["value"]
    actual = inputs.get(key)
    if actual is None:
        return []
    ok = _compare(op, actual, expected)
    if not ok:
        return []
    return [cond.get("label", f"{key} {op} {expected}（实际值：{actual}）")]


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == ">=":
        return actual >= expected
    if op == "<=":
        return actual <= expected
    if op == ">":
        return actual > expected
    if op == "<":
        return actual < expected
    raise ValueError(f"不支持的比较运算符: {op}")
