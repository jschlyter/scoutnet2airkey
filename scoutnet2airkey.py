import argparse
import json
import logging

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
    parser.add_argument("--dump", dest="dump")
    parser.add_argument("--load", dest="load")
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

    for k, v in key_holders.items():
        print(v)


if __name__ == "__main__":
    main()
