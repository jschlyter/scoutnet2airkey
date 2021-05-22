#!/usr/bin/env python3

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import yaml

import requests


DEFAULT_CONFIG_FILE = "scoutnet2airkey.yaml"


@dataclass(frozen=True)
class ScoutnetRole:
    role_id: int
    role_key: str

    @classmethod
    def from_data(cls, data):
        return cls(
            role_id=data.get("role_id"),
            role_key=data.get("role_key"),
        )


@dataclass(frozen=True)
class ScoutnetRoles:
    groups: Dict[int, List[ScoutnetRole]] = field(default_factory=list)
    troops: Dict[int, List[ScoutnetRole]] = field(default_factory=list)
    role_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_data(cls, data):
        if len(data) == 0:
            return cls()
        role_groups = {}
        for org_id, group_data in data.get("group", {}).items():
            role_groups[int(org_id)] = [
                ScoutnetRole.from_data(v) for v in group_data.values()
            ]
        role_troops = {}
        for troop_id, group_data in data.get("troop", {}).items():
            role_troops[int(troop_id)] = [
                ScoutnetRole.from_data(v) for v in group_data.values()
            ]
        roles_any = set()
        for k, rs in role_groups.items():
            for r in rs:
                roles_any.add(r.role_key)
        for k, rs in role_troops.items():
            for r in rs:
                roles_any.add(r.role_key)
        return cls(groups=role_groups, troops=role_troops, role_ids=list(roles_any))


@dataclass(frozen=True)
class ScoutnetMember:
    member_no: int
    first_name: Optional[str]
    last_name: Optional[str]
    contact_mobile_phone: Optional[str]
    roles: ScoutnetRoles

    def __repr__(self):
        return ", ".join(
                [
                    str(self.member_no),
                    self.first_name,
                    self.last_name,
                    self.contact_mobile_phone,
                ]
            )
        

    @staticmethod
    def get_data(field: str, data: dict):
        if field in data:
            return data[field]["value"]

    @classmethod
    def from_data(cls, data):
        return cls(
            member_no=int(cls.get_data("member_no", data)),
            first_name=cls.get_data("first_name", data),
            last_name=cls.get_data("last_name", data),
            contact_mobile_phone=convert_to_e164(
                cls.get_data("contact_mobile_phone", data)
            ),
            roles=ScoutnetRoles.from_data(cls.get_data("roles", data)),
        )


def convert_to_e164(phone: Optional[str]) -> Optional[str]:
    if phone:
        phone = re.sub(r"[\-\s]", "", phone)
        phone = re.sub(r"^0", "+46", phone)
    return phone


def get_members_from_scoutnet(
    api_endpoint: str, api_id: str, api_key: str, output: Optional[str]
):
    session = requests.Session()
    session.auth = (api_id, api_key)

    response = session.get(f"{api_endpoint}/group/memberlist")
    response.raise_for_status()
    members = response.json()

    if output:
        with open(output, "wt") as d:
            json.dump(members, d)
    return members


def main() -> None:
    """main"""

    parser = argparse.ArgumentParser(description="Scoutnet to EVVA Airkey integration")

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Test mode (no changes written)",
    )
    parser.add_argument(
        "--verbose", dest="verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--debug", dest="debug", action="store_true", help="Enable debugging output"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    with open(DEFAULT_CONFIG_FILE, "rt") as config_file:
        config = yaml.safe_load(config_file)

    # scoutnet_data = get_members_from_scoutnet(
    #    api_endpoint=config["scoutnet"]["api_endpoint"],
    #    api_id=config["scoutnet"]["api_id"],
    #    api_key=config["scoutnet"]["api_key"],
    #    output="dump.json",
    # )

    with open("dump.json", "rt") as d:
        scoutnet_data = json.load(d)

    members = {
        int(k): ScoutnetMember.from_data(v) for k, v in scoutnet_data["data"].items()
    }

    roles_with_key = set(list(config["airkey"]["roles"]))
    keyholders = {}

    for i, member in members.items():
        if member.roles.groups or member.roles.troops:
            if set(member.roles.role_ids) & roles_with_key:
                if member.contact_mobile_phone:
                    keyholders[i] = member

    for i, member in keyholders.items():
        print(member)


if __name__ == "__main__":
    main()
