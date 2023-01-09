import argparse
import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set

import airkey
import yaml
from scoutnet import ScoutnetClient, ScoutnetMember

DEFAULT_CONFIG_FILE = "scoutnet2airkey.yaml"
DEFAULT_LANGUAGE = "sv-SE"
DEFAULT_LIMIT = 100


def dump_data(client: ScoutnetClient, filename: str):
    memberlist_data = client.memberlist()
    customlists_data = client.customlists()

    client.memberlist = lambda: memberlist_data
    client.customlists = lambda: customlists_data

    with open(filename, "wt") as dump_file:
        json.dump(
            {"memberlist": memberlist_data, "customlists": customlists_data}, dump_file
        )


def load_data(client: ScoutnetClient, filename: str):

    with open(filename, "rt") as dump_file:
        dump = json.load(dump_file)

    memberlist_data = dump["memberlist"]
    customlists_data = dump["customlists"]

    client.memberlist = lambda: memberlist_data
    client.customlists = lambda: customlists_data


def get_key_holders(
    client: ScoutnetClient, holders: Set[str]
) -> Dict[int, ScoutnetMember]:
    """Get key holders from Scoutnet"""

    members = client.get_all_members()
    key_holders_aliases = set(holders)

    key_holders_list_ids = []
    for list_data in client.get_all_lists(fetch_members=False).values():
        if key_holders_aliases & set(list_data.aliases):
            key_holders_list_ids.append(list_data.id)

    key_holders = {}
    for list_id, v in client.get_all_lists(
        fetch_members=True, list_ids=key_holders_list_ids
    ).items():
        for member_id, member in v.members.items():
            if member_id not in key_holders:
                if member_id in members:
                    key_holders[member_id] = members[member_id]

    if len(key_holders) == 0:
        raise RuntimeError("No key holders!")

    return key_holders


class ScoutnetAirkey(object):
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        scoutnet_users: dict,
        dry_run: bool = True,
    ):
        conf = airkey.Configuration()
        conf.host = endpoint
        self.api_client = airkey.ApiClient(
            conf, header_name="X-API-Key", header_value=api_key
        )
        self.dry_run = dry_run

        self.logger = logging.getLogger(__name__).getChild(self.__class__.__name__)
        if self.dry_run:
            self.logger = self.logger.getChild("DryRun")

        self.scoutnet_users = scoutnet_users

        self.person_id_to_scoutnet_id = {}
        self.persons_by_person_id = {}
        self.persons_by_scoutnet_id = {}

        self.phones_by_medium_id = {}
        self.phones_by_scoutnet_id = {}
        self.phone_to_person_id = {}

        self.auth_by_auth_id = {}
        self.auth_by_scoutnet_id = defaultdict(list)

    def _fetch_persons(self):
        """Fetch persons from Airkey"""
        self.logger.debug("Fetch persons")
        api = airkey.PersonsApi(api_client=self.api_client)
        persons = []
        offset = 0
        limit = DEFAULT_LIMIT
        while True:
            res = api.get_persons(offset=offset, limit=limit)
            if not len(res.person_list):
                break
            persons.extend(res.person_list)
            offset += limit
        for p in persons:
            self.persons_by_person_id[p.id] = p
            if p.secondary_identification:
                scoutnet_id = int(p.secondary_identification)
                self.persons_by_scoutnet_id[scoutnet_id] = p
                self.person_id_to_scoutnet_id[p.id] = scoutnet_id
                if scoutnet_id in self.scoutnet_users:
                    phone_number = self.scoutnet_users[scoutnet_id].contact_mobile_phone
                    self.phone_to_person_id[phone_number] = p.id
                else:
                    self.logger.debug("%d not in Scoutnet (ignored)", scoutnet_id)

    def _fetch_medium(self):
        """Fetch mediums from Airkey"""
        self.logger.debug("Fetch medium")
        self._fetch_persons()
        api = airkey.MediaApi(api_client=self.api_client)
        medium = []
        offset = 0
        limit = DEFAULT_LIMIT
        while True:
            res = api.get_phones(offset=offset, limit=limit)
            if not len(res.medium_list):
                break
            medium.extend(res.medium_list)
            offset += limit
        for m in medium:
            self.phones_by_medium_id[m.id] = m
            if m.person_id:
                p = self.persons_by_person_id[m.person_id]
                if p.secondary_identification:
                    scoutnet_id = int(p.secondary_identification)
                    self.phones_by_scoutnet_id[scoutnet_id] = m

    def _fetch_auth(self):
        """Fetch authorizations from Airkey"""
        self.logger.debug("Fetch authorizations")
        self._fetch_persons()
        api = airkey.AuthorizationsApi(api_client=self.api_client)
        authorizations = []
        offset = 0
        limit = DEFAULT_LIMIT
        while True:
            res = api.get_authorizations(offset=offset, limit=limit)
            if not len(res.authorizations):
                break
            authorizations.extend(res.authorizations)
            offset += limit
        for a in authorizations:
            if a.current_state == "DELETED":
                continue
            self.auth_by_auth_id[a.id] = a
            try:
                person = self.persons_by_person_id[a.person_id]
                if person.secondary_identification:
                    scoutnet_id = int(person.secondary_identification)
                    self.auth_by_scoutnet_id[scoutnet_id].append(a)
            except KeyError as exc:
                self.logger.warning("No person for authorization %s", a.id)

    def sync_persons(
        self,
        create_persons: bool = False,
        update_persons: bool = False,
        delete_persons: bool = False,
        deauthorize_persons: bool = False,
    ):
        """Sync persons with Airkey"""
        self.logger.debug("Sync persons")

        self._fetch_persons()

        api = airkey.PersonsApi(api_client=self.api_client)

        airkey_ids = set(self.persons_by_scoutnet_id.keys())
        scoutnet_ids = set(self.scoutnet_users.keys())

        # Update existing users
        if update_persons:
            existing_ids = scoutnet_ids & airkey_ids
            req_update = []
            for i in existing_ids:
                if (
                    self.scoutnet_users[i].first_name
                    != self.persons_by_scoutnet_id[i].first_name
                    or self.scoutnet_users[i].last_name
                    != self.persons_by_scoutnet_id[i].last_name
                ):
                    self.logger.info(
                        "Update user %d (%s %s)",
                        i,
                        self.scoutnet_users[i].first_name,
                        self.scoutnet_users[i].last_name,
                    )
                    self.persons_by_scoutnet_id[i].first_name = self.scoutnet_users[
                        i
                    ].first_name
                    self.persons_by_scoutnet_id[i].last_name = self.scoutnet_users[
                        i
                    ].last_name
                    req_update.append(self.persons_by_scoutnet_id[i])
            if req_update and not self.dry_run:
                api.update_persons(req_update)

        # Create new users
        if create_persons:
            created_ids = scoutnet_ids - airkey_ids
            req_create = []
            for i in created_ids:
                self.logger.info(
                    "Create user %d (%s %s)",
                    i,
                    self.scoutnet_users[i].first_name,
                    self.scoutnet_users[i].last_name,
                )
                req_create.append(
                    airkey.models.PersonCreate(
                        first_name=self.scoutnet_users[i].first_name,
                        last_name=self.scoutnet_users[i].last_name,
                        secondary_identification=str(i),
                        correspondence_language_code=DEFAULT_LANGUAGE,
                    )
                )
            if req_create and not self.dry_run:
                api.create_persons(req_create)

        # Deauthorize removed users
        if deauthorize_persons:
            deleted_ids = airkey_ids - scoutnet_ids
            req_deauthorize = []
            auth_api = airkey.AuthorizationsApi(api_client=self.api_client)
            for i in deleted_ids:
                person_id = self.persons_by_scoutnet_id[i].id
                res = auth_api.get_authorizations(person_id=person_id)
                areas = [
                    a.area.name
                    for a in (res.authorizations or [])
                    if not a.deletion_requested
                ]
                if areas:
                    self.logger.info(
                        "User %d (%s %s) should no longer have access to %s",
                        i,
                        self.persons_by_scoutnet_id[i].first_name,
                        self.persons_by_scoutnet_id[i].last_name,
                        areas,
                    )
                    deauthorizations = [
                        airkey.models.AuthorizationDelete(
                            id=a.id, deletion_requested=True
                        )
                        for a in res.authorizations
                    ]
                    req_deauthorize.extend(deauthorizations)
            if req_deauthorize and not self.dry_run:
                auth_api.delete_authorization(req_deauthorize)

        # Delete removed users
        if delete_persons:
            deleted_ids = airkey_ids - scoutnet_ids
            req_delete = []
            for i in deleted_ids:
                self.logger.info(
                    "Delete user %d (%s %s)",
                    i,
                    self.persons_by_scoutnet_id[i].first_name,
                    self.persons_by_scoutnet_id[i].last_name,
                )
                req_delete.append(self.persons_by_scoutnet_id[i].id)
            if req_delete and not self.dry_run:
                api.delete_persons(req_delete)

        self._fetch_persons()

    def sync_phones(
        self,
        create_phones: bool = False,
        update_phones: bool = False,
        delete_phones: bool = False,
    ):
        """Sync phones with Airkey"""
        self.logger.debug("Sync phones")

        self._fetch_persons()
        self._fetch_medium()

        api = airkey.MediaApi(api_client=self.api_client)

        airkey_ids = set(self.phones_by_scoutnet_id.keys())
        scoutnet_ids = set(self.scoutnet_users.keys())

        # Update existing phones
        if update_phones:
            existing_ids = scoutnet_ids & airkey_ids
            req_update = []
            for i in existing_ids:
                if (
                    self.scoutnet_users[i].contact_mobile_phone
                    and self.phones_by_scoutnet_id[i].phone_number
                    != self.scoutnet_users[i].contact_mobile_phone
                ):
                    self.logger.info(
                        "Update phone %d (%s)",
                        i,
                        self.scoutnet_users[i].contact_mobile_phone,
                    )
                    self.phones_by_scoutnet_id[i].phone_number = self.scoutnet_users[
                        i
                    ].contact_mobile_phone
                    req_update.append(self.phones_by_scoutnet_id[i])
            if req_update and not self.dry_run:
                api.update_phones(req_update)

        # Create new phones
        if create_phones:
            created_ids = scoutnet_ids - airkey_ids
            req_create = []
            for i in created_ids:
                if self.scoutnet_users[i].contact_mobile_phone:
                    self.logger.info(
                        "Create phone %d (%s)",
                        i,
                        self.scoutnet_users[i].contact_mobile_phone,
                    )
                    req_create.append(
                        airkey.models.PhoneCreate(
                            phone_number=self.scoutnet_users[i].contact_mobile_phone,
                        )
                    )
                else:
                    self.logger.warning(
                        "Skipping user without phone %d (%s %s)",
                        i,
                        self.scoutnet_users[i].first_name,
                        self.scoutnet_users[i].last_name,
                    )
            if req_create:
                res = api.create_phones(req_create)
                self._fetch_medium()

                req_assign = []
                for phone in res:
                    person_id = self.phone_to_person_id.get(phone.phone_number)
                    if person_id:
                        self.logger.info("Assigning phone %s", phone.phone_number)
                        req_assign.append(
                            airkey.models.MediumAssignment(
                                medium_id=phone.id, person_id=person_id
                            )
                        )
                        self.phones_by_medium_id[phone.id] = phone
                    else:
                        self.logger.warning(
                            "Unable to find person for %s", phone.phone_number
                        )
                if req_assign and not self.dry_run:
                    res = api.assign_owner_to_medium(req_assign)
                    self.logger.info(
                        "Sending registration codes to %d new phones", len(res)
                    )
                    for m in res:
                        self.send_registration_code(api, m.medium_id)

        # Delete removed phones
        if delete_phones:
            deleted_ids = airkey_ids - scoutnet_ids
            req_delete = []
            for i in deleted_ids:
                self.logger.info(
                    "Delete phone %d (%s)",
                    i,
                    self.phones_by_scoutnet_id[i].phone_number,
                )
                req_delete.append(self.phones_by_scoutnet_id[i].id)
            if req_delete and not self.dry_run:
                api.delete_phones(req_delete)

    def delete_unassigned_phones(self):
        """Delete unassigned phones from Airkey"""
        self.logger.debug("Delete unassigned phones")
        api = airkey.MediaApi(api_client=self.api_client)
        medium = []
        offset = 0
        limit = DEFAULT_LIMIT
        while True:
            res = api.get_phones(offset=offset, limit=limit)
            if not len(res.medium_list):
                break
            medium.extend(res.medium_list)
            offset += limit
        unassigned_phones = []
        for m in medium:
            if not m.person_id:
                self.logger.warning(
                    "Deleting unassigned phone %d, %s", m.id, m.phone_number
                )
                unassigned_phones.append(m.id)
        if unassigned_phones and not self.dry_run:
            api = airkey.MediaApi(api_client=self.api_client)
            api.delete_phones(unassigned_phones)

    def sync_auth(
        self,
        area_ids: List = [],
        create_auth: bool = False,
        update_auth: bool = False,
        delete_auth: bool = False,
    ):
        """Sync auth with Airkey"""
        self.logger.debug("Sync auth")

        self._fetch_persons()
        self._fetch_medium()
        self._fetch_auth()

        api = airkey.AuthorizationsApi(api_client=self.api_client)
        self.phones_by_medium_id = {}

        if create_auth:
            req_create = []
            for scoutnet_id, person in self.persons_by_scoutnet_id.items():
                if scoutnet_id not in self.auth_by_scoutnet_id:

                    phone = self.phones_by_scoutnet_id.get(scoutnet_id)
                    if not phone:
                        self.logger.debug(
                            "No phone medium for %d, %s %s",
                            scoutnet_id,
                            person.first_name,
                            person.last_name,
                        )
                        continue

                    for area_id in area_ids:
                        self.logger.info(
                            "Create auth for %d, %s %s, area %d",
                            scoutnet_id,
                            person.first_name,
                            person.last_name,
                            area_id,
                        )

                        req_create.append(
                            airkey.models.AuthorizationChange(
                                authorization_create_list=[
                                    airkey.models.AuthorizationCreate(
                                        authorization_info_list=[
                                            airkey.models.AuthorizationInfo(
                                                type="PERMANENT"
                                            )
                                        ],
                                        medium_id=phone.id,
                                        area_id=area_id,
                                    ),
                                ],
                                authorization_update_list=[],
                            )
                        )
                else:
                    self.logger.debug(
                        "Existing auth for %d, %s %s",
                        scoutnet_id,
                        person.first_name,
                        person.last_name,
                    )

            if req_create and not self.dry_run:
                for a in req_create:
                    api.create_or_update_authorizations_with_advanced_options(a)

        if update_auth:
            self.logger.warning("Authorization updates not yet implemented")

        if delete_auth:
            self.logger.warning("Authorization deletions not yet implemented")

    def send_pending_registration_codes(self, limit: Optional[int] = None):
        """Send registration codes"""
        self.logger.debug("Send registration codes")
        self._fetch_medium()
        count = 0
        api = airkey.MediaApi(api_client=self.api_client)
        for medium_id in self.phones_by_medium_id.keys():
            if limit is None or limit > 0:
                sent = self.send_registration_code(api, medium_id)
                count += 1 if sent else 0
            if sent and limit is not None and limit > 0:
                limit -= 1
        self.logger.info("%d codes sent", count)

    def list_pending_registration_codes(self):
        self.logger.debug("List pending registration codes")
        self._fetch_medium()
        api = airkey.MediaApi(api_client=self.api_client)
        count = 0
        for medium_id in self.phones_by_medium_id.keys():
            phone = self.phones_by_medium_id[medium_id]
            if (
                phone.medium_identifier is None
                and phone.pairing_code_valid_until is not None
            ):
                count += 1
                person = self.persons_by_person_id.get(phone.person_id)
                scoutnet_id = self.person_id_to_scoutnet_id.get(phone.person_id)
                scout = self.scoutnet_users.get(scoutnet_id)

                if scout:
                    print(f"{phone.phone_number} ({scout.display_name}) {scout.email}")
                else:
                    print(f"{phone.phone_number} (anonymous)")
        print(
            f"{count} pending registrations (total {len(self.phones_by_medium_id)} phones)"
        )

    def send_registration_code(self, api, medium_id: int) -> bool:
        self.logger.debug("Send registration code for %d", medium_id)
        phone = self.phones_by_medium_id[medium_id]
        if phone.medium_identifier is None:

            person = self.persons_by_person_id.get(phone.person_id)
            scoutnet_id = self.person_id_to_scoutnet_id.get(phone.person_id)
            scout = self.scoutnet_users.get(scoutnet_id)

            if phone.pairing_code_valid_until is not None:
                self.logger.info(
                    "Pending registration exists for %s (%s)",
                    phone.phone_number,
                    scout.display_name,
                )
            else:
                self.logger.info(
                    "Sending new registration code to %s (%s)",
                    phone.phone_number,
                    scout.display_name,
                )
                if not self.dry_run:
                    api.generate_pairing_code_for_phone(phone.id)
                    api.send_registration_code_to_phone(phone.id)
                    return True
        else:
            self.logger.debug("Already registered %s", phone.phone_number)
        return False


def main() -> None:
    """Main function"""

    parser = argparse.ArgumentParser(description="Scoutnet EVVA Airkey Integration")

    parser.add_argument("commands", nargs="+", choices=["sync", "sms", "pending"])
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Test mode (no changes written)",
    )
    parser.add_argument(
        "--silent",
        dest="silent",
        action="store_true",
        help="Disable non-critical warnings",
    )
    parser.add_argument("--dump", dest="dump", metavar="filename")
    parser.add_argument("--load", dest="load", metavar="filename")
    parser.add_argument(
        "--limit", dest="limit", type=int, help="Limit number of operations"
    )
    parser.add_argument(
        "--debug", dest="debug", action="store_true", help="Enable debugging output"
    )
    parser.add_argument(
        "--delete", dest="delete", action="store_true", help="Delete resources"
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.silent:
        logging.basicConfig(level=logging.WARNING)
    else:
        logging.basicConfig(level=logging.INFO)

    with open(DEFAULT_CONFIG_FILE, "rt") as config_file:
        config = yaml.safe_load(config_file)

    scoutnet_client = ScoutnetClient(
        api_endpoint=config["scoutnet"].get("api_endpoint"),
        api_id=config["scoutnet"]["api_id"],
        api_key_memberlist=config["scoutnet"]["api_key_memberlist"],
        api_key_customlists=config["scoutnet"]["api_key_customlists"],
    )

    if args.dump:
        dump_data(scoutnet_client, args.dump)
    elif args.load:
        load_data(scoutnet_client, args.load)

    key_holders_aliases = set(config["airkey"]["holders"])
    key_holders = get_key_holders(scoutnet_client, key_holders_aliases)

    airkey_client = ScoutnetAirkey(
        endpoint=config["airkey"]["endpoint"],
        api_key=config["airkey"]["api_key"],
        scoutnet_users=key_holders,
        dry_run=args.dry_run,
    )

    if "sync" in args.commands:
        airkey_client.sync_persons(create_persons=True, update_persons=True)
        airkey_client.sync_phones(
            create_phones=True, update_phones=True, delete_phones=args.delete
        )
        airkey_client.delete_unassigned_phones()
        airkey_client.sync_persons(delete_persons=args.delete, deauthorize_persons=True)

        areas_ids = config["airkey"].get("areas")
        if areas_ids:
            airkey_client.sync_auth(area_ids=areas_ids)

    if "sms" in args.commands:
        airkey_client.send_pending_registration_codes(limit=args.limit)

    if "pending" in args.commands:
        airkey_client.list_pending_registration_codes()


if __name__ == "__main__":
    main()
