"""跨机构统一身份：企业筛查与医院之间不重复建档。

合并分两档：
- 确定性匹配（同一身份证件令牌）：自动挂接到既有统一档案；
- 相似匹配（姓名/出生日期/性别/电话一致但无证件令牌）：标记为“待核实”，
  需医护人员核实后才能正式合并，避免把两个真人错误并档。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Alias:
    """某机构本地系统中的一条身份记录。"""

    organization: str          # 如 "企业职业健康中心" / "市眼科医院"
    local_id: str              # 该机构本地档案号
    name: str
    birth_date: date
    gender: str
    id_token: str = ""         # 身份证件令牌（演示数据为合成值），可为空
    phone: str = ""
    established: date | None = None


@dataclass
class PersonRecord:
    person_id: str
    aliases: list[Alias] = field(default_factory=list)
    merge_status: str = "active"  # active / pending_verification

    def organizations(self) -> list[str]:
        return sorted({a.organization for a in self.aliases})

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "merge_status": self.merge_status,
            "organizations": self.organizations(),
            "aliases": [
                {
                    "organization": a.organization,
                    "local_id": a.local_id,
                    "name": a.name,
                    "birth_date": a.birth_date.isoformat(),
                    "gender": a.gender,
                    "phone": a.phone,
                }
                for a in self.aliases
            ],
        }


def _norm_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


class IdentityRegistry:
    def __init__(self) -> None:
        self._persons: list[PersonRecord] = []
        self._by_token: dict[str, str] = {}
        self._next_id = 1

    @property
    def persons(self) -> list[PersonRecord]:
        return list(self._persons)

    def _new_person_id(self) -> str:
        pid = f"PERSON-{self._next_id:04d}"
        self._next_id += 1
        return pid

    def register(self, alias: Alias) -> tuple[PersonRecord, str]:
        """登记一条机构身份，返回(统一档案, 挂接方式)。

        挂接方式：created 新建 / linked_token 证件令牌自动合并 /
        pending_demographic 人口学信息疑似一致，待人工核实。
        """
        if alias.id_token and alias.id_token in self._by_token:
            person = self._find(self._by_token[alias.id_token])
            if not any(
                a.organization == alias.organization and a.local_id == alias.local_id
                for a in person.aliases
            ):
                person.aliases.append(alias)
            return person, "linked_token"

        match = self._demographic_match(alias)
        if match is not None:
            # 不立即并档：挂为待核实，交由工作人员确认。
            match.merge_status = "pending_verification"
            if not any(
                a.organization == alias.organization and a.local_id == alias.local_id
                for a in match.aliases
            ):
                match.aliases.append(alias)
            return match, "pending_demographic"

        person = PersonRecord(person_id=self._new_person_id())
        person.aliases.append(alias)
        if alias.id_token:
            self._by_token[alias.id_token] = person.person_id
        self._persons.append(person)
        return person, "created"

    def confirm_merge(self, person_id: str, id_token: str) -> PersonRecord:
        """工作人员核验证件后，确认待核实并档并补登证件令牌。"""
        person = self._find(person_id)
        if person.merge_status != "pending_verification":
            raise ValueError("该档案没有待核实的并档请求")
        if id_token in self._by_token and self._by_token[id_token] != person_id:
            raise ValueError("证件令牌已属于另一份档案，不能合并")
        person.merge_status = "active"
        for alias in person.aliases:
            if not alias.id_token:
                object.__setattr__(alias, "id_token", id_token)
        self._by_token[id_token] = person_id
        return person

    def _demographic_match(self, alias: Alias) -> PersonRecord | None:
        for person in self._persons:
            for existing in person.aliases:
                if (
                    existing.name == alias.name
                    and existing.birth_date == alias.birth_date
                    and existing.gender == alias.gender
                    and _norm_phone(existing.phone)
                    and _norm_phone(existing.phone) == _norm_phone(alias.phone)
                ):
                    return person
        return None

    def _find(self, person_id: str) -> PersonRecord:
        for person in self._persons:
            if person.person_id == person_id:
                return person
        raise KeyError(f"未找到统一档案: {person_id}")
