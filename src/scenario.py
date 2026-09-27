"""从 fixtures/scenario.json 装配端到端演示：

身份登记与跨机构合并 → 授权 → 时间线摄入（按当时有效规则追加结论）
→ 病程回顾 / 差异化提醒 / 雇主群体视图。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from . import access as access_mod
from .care import ingest_event, patient_reminders, review_narrative
from .identity import Alias, IdentityRegistry
from .records import CareTimeline
from .rules import RuleBook


def _d(value: str) -> date:
    return date.fromisoformat(value)


def load_scenario(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_identities(data: dict[str, Any]) -> tuple[IdentityRegistry, dict[str, str]]:
    """登记场景中的机构身份，返回(注册处, person_key→统一档案号)。"""
    registry = IdentityRegistry()
    person_ids: dict[str, str] = {}
    identity_results: list[dict[str, str]] = []
    for item in data["identities"]:
        alias = Alias(
            organization=item["organization"],
            local_id=item["local_id"],
            id_token=item.get("id_token", ""),
            name=item["name"],
            birth_date=_d(item["birth_date"]),
            gender=item["gender"],
            phone=item.get("phone", ""),
            established=_d(item["established"]),
        )
        person, mode = registry.register(alias)
        person_ids[item["person_key"]] = person.person_id
        identity_results.append(
            {
                "organization": item["organization"],
                "local_id": item["local_id"],
                "person_id": person.person_id,
                "mode": mode,
                "merge_status": person.merge_status,
            }
        )
        if mode == "pending_demographic" and item.get("confirm_with_token"):
            registry.confirm_merge(person.person_id, item["confirm_with_token"])
    return registry, person_ids


def build_consents(
    data: dict[str, Any], person_ids: dict[str, str]
) -> access_mod.AccessControl:
    ac = access_mod.AccessControl()
    for item in data.get("consents", []):
        ac.grant(
            person_id=person_ids[item["person_key"]],
            scope=item["scope"],
            grantee=item["grantee"],
            day=_d(item["granted_on"]),
            purpose=item["purpose"],
        )
    return ac


def build_timelines(
    data: dict[str, Any],
    person_ids: dict[str, str],
    rulebook: RuleBook,
) -> dict[str, CareTimeline]:
    timelines: dict[str, CareTimeline] = {}
    for key, events in data["timelines"].items():
        timeline = CareTimeline(person_id=person_ids[key])
        for item in events:
            ingest_event(
                timeline=timeline,
                rulebook=rulebook,
                day=_d(item["date"]),
                event_type=item["event_type"],
                actor=item["actor"],
                payload=item.get("payload"),
                source=item.get("source", ""),
                note=item.get("note", ""),
            )
        timeline.verify()
        timelines[key] = timeline
    return timelines


def employer_view(
    data: dict[str, Any], rulebook: RuleBook | None = None
) -> dict[str, Any]:
    cohort = data["employer_cohort"]
    person_levels: dict[str, str] = {}
    for level, count in cohort["level_counts"].items():
        for i in range(count):
            person_levels[f"cohort-member-{level}-{i}"] = level
    return access_mod.population_risk(person_levels, _d(cohort["as_of"]))


def build_all(base_dir: str | Path | None = None) -> dict[str, Any]:
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    rulebook = RuleBook.from_path(base / "fixtures" / "rules.json")
    data = load_scenario(base / "fixtures" / "scenario.json")
    registry, person_ids = build_identities(data)
    consents = build_consents(data, person_ids)
    timelines = build_timelines(data, person_ids, rulebook)

    today = _d(data["today"])
    wanglei = timelines["wanglei"]
    linchen = timelines["linchen"]
    return {
        "rulebook": rulebook,
        "registry": registry,
        "person_ids": person_ids,
        "access": consents,
        "timelines": timelines,
        "wanglei_review": review_narrative(wanglei),
        "wanglei_reminders": [
            {"priority": r.priority, "title": r.title, "detail": r.detail}
            for r in patient_reminders(wanglei, today)
        ],
        "linchen_reminders": [
            {"priority": r.priority, "title": r.title, "detail": r.detail}
            for r in patient_reminders(linchen, today)
        ],
        "employer_view": employer_view(data, rulebook),
    }


def main() -> None:
    result = build_all()
    print(json.dumps(
        {
            "identities": [p.as_dict() for p in result["registry"].persons],
            "wanglei_review": result["wanglei_review"],
            "wanglei_reminders": result["wanglei_reminders"],
            "linchen_reminders": result["linchen_reminders"],
            "employer_view": result["employer_view"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
