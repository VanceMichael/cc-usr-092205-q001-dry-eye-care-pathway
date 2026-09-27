"""授权访问与隐私边界。

规则：
- 患者本人随时可查看自己的全部记录；
- 医护人员跨机构查看个体记录，必须有本人就该用途授予的有效授权
  （如随转诊签署的 record:transfer / record:view），授权可撤回但不可删除；
- 雇主角色在任何情况下都拿不到个体记录，只能取得群体风险分布；
  群体结果做小格子隐匿（人数不足阈值的分组合并隐匿），防止反推个人。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# 群体统计最小格子人数：少于该值的分组不对外披露。
MIN_CELL_SIZE = 5

SCOPE_VIEW_RECORD = "record:view"
SCOPE_TRANSFER_RECORD = "record:transfer"
SCOPE_POPULATION_RISK = "population:risk"

ROLE_PATIENT = "patient"
ROLE_CLINICIAN = "clinician"
ROLE_EMPLOYER = "employer"

LEVELS = ["lifestyle", "review", "medical", "urgent"]
LEVEL_NAMES_ZH = {
    "lifestyle": "生活干预",
    "review": "安排复查",
    "medical": "眼科就医",
    "urgent": "紧急就医",
}


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ConsentGrant:
    person_id: str
    scope: str
    grantee: str            # 形如 "clinician@市眼科医院"
    granted_on: date
    purpose: str
    revoked_on: date | None = None


@dataclass
class AccessControl:
    _grants: list[ConsentGrant] = field(default_factory=list)

    def grant(
        self, person_id: str, scope: str, grantee: str, day: date, purpose: str
    ) -> ConsentGrant:
        g = ConsentGrant(
            person_id=person_id,
            scope=scope,
            grantee=grantee,
            granted_on=day,
            purpose=purpose,
        )
        self._grants.append(g)
        return g

    def revoke(self, person_id: str, scope: str, grantee: str, day: date) -> None:
        """撤回授权：追加撤回标记，原有授权记录保留备查。"""
        for g in reversed(self._grants):
            if (
                g.person_id == person_id
                and g.scope == scope
                and g.grantee == grantee
                and g.revoked_on is None
            ):
                object.__setattr__(g, "revoked_on", day)
                return
        raise KeyError("没有可撤回的有效授权")

    def is_authorized(
        self,
        person_id: str,
        scope: str,
        role: str,
        grantee: str,
        day: date,
    ) -> bool:
        if role == ROLE_EMPLOYER and scope != SCOPE_POPULATION_RISK:
            return False
        if role == ROLE_PATIENT:
            return scope == SCOPE_VIEW_RECORD
        return any(
            g.person_id == person_id
            and g.scope == scope
            and g.grantee == grantee
            and g.granted_on <= day
            and (g.revoked_on is None or g.revoked_on > day)
            for g in self._grants
        )

    def require_person_record(
        self,
        person_id: str,
        role: str,
        grantee: str,
        day: date,
        scope: str = SCOPE_VIEW_RECORD,
    ) -> None:
        """个体健康信息访问闸门：雇主角色在此被拒绝。"""
        if role == ROLE_EMPLOYER:
            raise AccessDenied("雇主只能获取群体风险，不得访问个体健康信息")
        if not self.is_authorized(person_id, scope, role, grantee, day):
            raise AccessDenied(
                f"{grantee} 缺少本人对 {scope} 的有效授权，不能查看该个体记录"
            )

    def grants_audit(self, person_id: str) -> list[dict[str, Any]]:
        return [
            {
                "scope": g.scope,
                "grantee": g.grantee,
                "granted_on": g.granted_on.isoformat(),
                "purpose": g.purpose,
                "revoked_on": g.revoked_on.isoformat() if g.revoked_on else None,
            }
            for g in self._grants
            if g.person_id == person_id
        ]


def population_risk(
    person_levels: dict[str, str], on_date: date
) -> dict[str, Any]:
    """生成雇主可见的群体风险分布（不含任何身份信息）。

    小格子隐匿采用“相邻高危桶向上合并”：人数不足阈值的层级并入更重一级
    （如 urgent 不足 5 人则与 medical 合并为“眼科就医及以上”），
    避免用大桶与合计相减反推出被隐匿小桶的人数。整体样本不足时全部隐匿。
    """
    total = len(person_levels)
    counts = {level: 0 for level in LEVELS}
    for level in person_levels.values():
        if level not in counts:
            raise ValueError(f"未知风险层级: {level}")
        counts[level] += 1

    if total < MIN_CELL_SIZE:
        buckets = [
            {
                "levels": [level],
                "level_name": LEVEL_NAMES_ZH[level],
                "count": None,
                "percent": None,
                "suppressed": True,
                "reason": f"群体总数少于{MIN_CELL_SIZE}人，为防止反推个人已隐匿",
            }
            for level in LEVELS
        ]
        need_medical_value: int | None = None
    else:
        # 自最高层级向下，把不足阈值的桶折叠进相邻的更重一级。
        folded: dict[str, int | None] = dict(counts)
        absorbed: dict[str, list[str]] = {level: [] for level in LEVELS}
        for i in range(len(LEVELS) - 1, 0, -1):
            top = LEVELS[i]
            if folded[top] is not None and folded[top] < MIN_CELL_SIZE:  # type: ignore[operator]
                folded[LEVELS[i - 1]] += folded[top]  # type: ignore[operator]
                absorbed[LEVELS[i - 1]].append(top)
                folded[top] = None

        buckets = []
        for level in LEVELS:
            n = folded[level]
            if n is None:
                continue
            extra_levels = absorbed[level]
            name = (
                LEVEL_NAMES_ZH[level] + "及以上"
                if extra_levels
                else LEVEL_NAMES_ZH[level]
            )
            buckets.append(
                {
                    "levels": [level] + extra_levels,
                    "level_name": name,
                    "count": n,
                    "percent": round(n * 100 / total, 1),
                    "suppressed": False,
                    "reason": None,
                }
            )
        need_medical = counts["medical"] + counts["urgent"]
        need_medical_value = need_medical if need_medical >= MIN_CELL_SIZE else None

    return {
        "as_of": on_date.isoformat(),
        "total_screened": total,
        "buckets": buckets,
        "need_medical_or_above": need_medical_value,
        "contained_individual_data": False,
    }
