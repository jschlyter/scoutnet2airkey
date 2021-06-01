import argparse
import json
import logging
from collections import defaultdict

import airkey_client as airkey
import yaml
from scoutnet import ScoutnetClient

DEFAULT_CONFIG_FILE = "scoutnet2airkey.yaml"


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


class ScoutnetAirkey(object):
    def __init__(
        self, endpoint: str, api_key: str, scoutnet_users: dict, dry_run: bool = True
    ):
        conf = airkey.Configuration()
        conf.host = endpoint
        self.api_client = airkey.ApiClient(
            conf, header_name="X-API-Key", header_value=api_key
        )
        self.dry_run = dry_run
        self.logger = logging.getLogger(__name__).getChild(self.__class__.__name__)

        self.scoutnet_users = scoutnet_users
        del self.scoutnet_users[3244499]

        self.persons_by_person_id = {}
        self.persons_by_scoutnet_id = {}
        self.phones_by_medium_id = {}
        self.phones_by_scoutnet_id = {}
        self.auth_by_auth_id = {}
        self.auth_by_scoutnet_id = defaultdict(list)

        self.fetch_persons()
        self.fetch_medium()
        self.fetch_auth()

    def fetch_persons(self):
        """Fetch persons from Airkey"""
        api = airkey.PersonsApi(api_client=self.api_client)
        res = api.get_persons()
        for p in res.person_list:
            self.persons_by_person_id[p.id] = p
            if p.secondary_identification:
                scoutnet_id = int(p.secondary_identification)
                self.persons_by_scoutnet_id[scoutnet_id] = p

    def fetch_medium(self):
        """Fetch mediums from Airkey"""
        api = airkey.MediaApi(api_client=self.api_client)
        res = api.get_phones()
        for m in res.medium_list:
            self.phones_by_medium_id[m.id] = m
            scoutnet_id = int(
                self.persons_by_person_id[m.person_id].secondary_identification
            )
            self.phones_by_scoutnet_id[scoutnet_id] = m

    def fetch_auth(self):
        """Fetch authorizations from Airkey"""
        api = airkey.AuthorizationsApi(api_client=self.api_client)
        res = api.get_authorizations()
        for a in res.authorizations:
            self.auth_by_auth_id[a.id] = a
            scoutnet_id = int(
                self.persons_by_person_id[a.person_id].secondary_identification
            )
            self.auth_by_scoutnet_id[scoutnet_id].append(a)

    def sync_persons(self):
        """Sync persons with Airkey"""
        api = airkey.PersonsApi(api_client=self.api_client)

        airkey_ids = set(self.persons_by_scoutnet_id.keys())
        scoutnet_ids = set(self.scoutnet_users.keys())

        deleted_ids = airkey_ids - scoutnet_ids
        created_ids = scoutnet_ids - airkey_ids
        existing_ids = scoutnet_ids & airkey_ids

        for i in existing_ids:
            if (
                self.scoutnet_users[i].first_name
                != self.persons_by_scoutnet_id[i].first_name
                or self.scoutnet_users[i].last_name
                != self.persons_by_scoutnet_id[i].last_name
            ):
                self.logger.info(
                    "Update user %d, set %s %s",
                    i,
                    self.scoutnet_users[i].first_name,
                    self.scoutnet_users[i].last_name,
                )
        for i in created_ids:
            self.logger.info(
                "Create user %d, %s %s",
                i,
                self.scoutnet_users[i].first_name,
                self.scoutnet_users[i].last_name,
            )
        for i in deleted_ids:
            self.logger.info(
                "Delete user %d, %s %s",
                i,
                self.persons_by_scoutnet_id[i].first_name,
                self.persons_by_scoutnet_id[i].last_name,
            )

    def sync_phones(self):
        """Sync phones with Airkey"""
        api = airkey.MediaApi(api_client=self.api_client)

        airkey_ids = set(self.phones_by_scoutnet_id.keys())
        scoutnet_ids = set(self.scoutnet_users.keys())

        deleted_ids = airkey_ids - scoutnet_ids
        created_ids = scoutnet_ids - airkey_ids
        existing_ids = scoutnet_ids & airkey_ids

        for i in existing_ids:
            if (
                self.phones_by_scoutnet_id[i].phone_number
                != self.scoutnet_users[i].contact_mobile_phone
            ):
                self.logger.info(
                    "Update phone %d, set %s (%s %s)",
                    i,
                    self.scoutnet_users[i].contact_mobile_phone,
                    self.scoutnet_users[i].first_name,
                    self.scoutnet_users[i].last_name,
                )

        for i in created_ids:
            if self.scoutnet_users[i].contact_mobile_phone:
                self.logger.info(
                    "Create phone %d, %s (%s %s)",
                    i,
                    self.scoutnet_users[i].contact_mobile_phone,
                    self.scoutnet_users[i].first_name,
                    self.scoutnet_users[i].last_name,
                )
            else:
                self.logger.warning(
                    "No phone for %d, %s %s",
                    i,
                    self.scoutnet_users[i].first_name,
                    self.scoutnet_users[i].last_name,
                )

        for i in deleted_ids:
            self.logger.info(
                "Delete phone %d, %s (%s %s)",
                i,
                self.phones_by_scoutnet_id[i].phone_number,
                self.persons_by_scoutnet_id[i].first_name,
                self.persons_by_scoutnet_id[i].last_name,
            )

    def sync_auth(self, scoutnet_users: dict):
        """Sync medias with Airkey"""
        api = airkey.AuthorizationsApi(api_client=self.api_client)


def main() -> None:
    """Main function"""

    parser = argparse.ArgumentParser(description="Scoutnet EVVA Airkey Integration")

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Test mode (no changes written)",
    )
    parser.add_argument(
        "--verbose", dest="verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument("--dump", dest="dump", metavar="filename")
    parser.add_argument("--load", dest="load", metavar="filename")
    parser.add_argument(
        "--airkey", dest="airkey", action="store_true", help="Provision to EVVA Airkey"
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

    scoutnet = ScoutnetClient(
        api_endpoint=config["scoutnet"].get("api_endpoint"),
        api_id=config["scoutnet"]["api_id"],
        api_key_memberlist=config["scoutnet"]["api_key_memberlist"],
        api_key_customlists=config["scoutnet"]["api_key_customlists"],
    )

    if args.dump:
        dump_data(scoutnet, args.dump)
    elif args.load:
        load_data(scoutnet, args.load)

    members = scoutnet.get_all_members()
    key_holders_aliases = set(set(config["airkey"]["holders"]))

    key_holders_list_ids = []
    for list_data in scoutnet.get_all_lists(fetch_members=False).values():
        if key_holders_aliases & set(list_data.aliases):
            key_holders_list_ids.append(list_data.id)

    key_holders = {}
    for list_id, v in scoutnet.get_all_lists(
        fetch_members=True, list_ids=key_holders_list_ids
    ).items():
        for member_id, member in v.members.items():
            if member_id not in key_holders:
                if member_id in members:
                    key_holders[member_id] = members[member_id]

    if len(key_holders) == 0:
        raise RuntimeError("No key holders!")

    if args.airkey:
        a = ScoutnetAirkey(
            endpoint=config["airkey"]["endpoint"],
            api_key=config["airkey"]["api_key"],
            scoutnet_users=key_holders,
        )
        a.sync_persons()
        a.sync_phones()


if __name__ == "__main__":
    main()
