"""Generate a STAC Item from a local NetCDF dataset directory."""

import argparse
import json
import logging
from pathlib import Path
import sys

import yaml

from esgcet.stac.stac_preview import stac_item_from_directory, validate_stac_item


def _config_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0", ""}:
            return False
    if value is None:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {value!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an ESGF-style STAC Item without publishing it."
    )
    parser.add_argument("dataset_dir", type=Path, help="Dataset version directory (v<version>)")
    parser.add_argument("--project", help="Project name; defaults to project in the config")
    parser.add_argument("--data-node", help="Data node; defaults to data_node in the config")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".esg" / "esg.yaml",
        help="Publisher YAML configuration",
    )
    parser.add_argument("--stac-api", help="Base URL used for STAC item links")
    parser.add_argument("--output", "-o", type=Path, help="Write the Item to this JSON file")
    parser.add_argument(
        "--valid",
        action="store_true",
        help="Validate the generated Item against its referenced ESGF schema",
    )
    parser.add_argument(
        "--fake-checksum",
        action="store_true",
        help="Skip file hashing and use a placeholder; requires --valid",
    )
    args = parser.parse_args()
    if args.fake_checksum and not args.valid:
        parser.error("--fake-checksum can only be used with --valid")
    if args.fake_checksum:
        print(
            "WARNING: placeholder checksums are used; file integrity is not checked.",
            file=sys.stderr,
        )

    with args.config.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}

    project = args.project or config.get("project")
    data_node = args.data_node or config.get("data_node")
    if not project:
        parser.error("--project is required when project is not set in the config")
    if not data_node:
        parser.error("--data-node is required when data_node is not set in the config")

    user_project_config = config.get("user_project_config") or {}
    cmip6_clone = config.get("cmip6_clone")
    if isinstance(cmip6_clone, str) and project.lower() == cmip6_clone.lower():
        user_project_config = {**user_project_config, "clone_project": "cmip6"}

    stac_config = config.get("stac_config") or {}
    if args.stac_api:
        stac_config = {**stac_config, "stac_api": args.stac_api}
    try:
        test = _config_bool(config.get("test", False), "test")
        disable_citation = _config_bool(
            config.get("disable_citation", False), "disable_citation"
        )
    except ValueError as error:
        parser.error(str(error))

    previous_log_disable = logging.root.manager.disable
    if args.valid:
        logging.disable(logging.CRITICAL)
    try:
        item = stac_item_from_directory(
            args.dataset_dir,
            project,
            data_node,
            config.get("data_roots", {}),
            stac_config=stac_config,
            user_project_config=user_project_config,
            index_node=config.get("index_node", ""),
            test=test,
            disable_citation=disable_citation,
            fake_checksum=args.fake_checksum,
        )
    finally:
        logging.disable(previous_log_disable)
    output = json.dumps(item, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    if args.valid:
        errors = validate_stac_item(item)
        if errors:
            print("FAIL")
            for error in errors:
                print(f"- {error}")
            return 1
        print("PASS")
        return 0
    if not args.output:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())