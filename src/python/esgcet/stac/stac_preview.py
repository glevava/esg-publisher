"""Build a STAC Item from a local NetCDF dataset directory."""

from hashlib import sha256
from pathlib import Path

import jsonschema
import requests
from jsonschema import FormatChecker

from esgcet.scan.mk_dataset import ESGPubMakeDataset
from esgcet.scan.mk_dataset_nc4 import ESGPubNC4Handler
from esgcet.stac.stac_converter import ESGSTACConverter
from esgcet.util.pid_cite_pub import ESGPubPidCite
from esgcet.util import logger
from esgcet.util.settings import DRS, PID_PREFIX


log = logger.ESGPubLogger()


def validate_stac_item(item: dict) -> list[str]:
    """Validate an item against its referenced ESGF project schema."""

    schema_url = next(
        (
            url
            for url in item.get("stac_extensions", [])
            if "/stac-transaction-api/" in url and url.endswith("/schema.json")
        ),
        None,
    )
    if schema_url is None:
        return ["No ESGF project schema URL found in stac_extensions."]

    try:
        response = requests.get(schema_url, timeout=30)
        response.raise_for_status()
        schema = response.json()
    except (requests.RequestException, ValueError) as error:
        return [f"Could not retrieve schema {schema_url}: {error}"]

    try:
        validator_class = jsonschema.validators.validator_for(schema)
        validator_class.check_schema(schema)
    except (jsonschema.exceptions.SchemaError, TypeError, ValueError) as error:
        return [f"Invalid schema {schema_url}: {error}"]

    validator = validator_class(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(item),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )

    def format_error(error) -> list[str]:
        if error.validator == "oneOf" and error.context:
            branch_index = 0 if item.get("type") == "Feature" else 1
            matching_branch = [
                child
                for child in error.context
                if child.schema_path and child.schema_path[0] == branch_index
            ]
            if matching_branch:
                return [
                    detail
                    for child in matching_branch
                    for detail in format_error(child)
                ]

        path = (
            "$" + "." + ".".join(str(part) for part in error.absolute_path)
            if error.absolute_path
            else "$"
        )
        if error.validator == "contains":
            contains_schema = error.schema.get("contains", {})
            required_rel = (
                contains_schema.get("properties", {}).get("rel", {}).get("const")
            )
            if required_rel:
                return [f"{path}: missing required link relation '{required_rel}'"]
        return [f"{path}: {error.message}"]

    return [detail for error in errors for detail in format_error(error)]


def _project_drs(project: str, user_project_config: dict | None) -> list[str]:
    project_key = project.lower()
    if project_key in DRS:
        return DRS[project_key]

    project_config = user_project_config or {}
    for key in (project, project_key, project.upper()):
        configured = project_config.get(key, {})
        if "DRS" in configured:
            return configured["DRS"]
        clone = configured.get("clone_project")
        if clone and clone.lower() in DRS:
            return DRS[clone.lower()]

    raise ValueError(f"No DRS configuration found for project {project}")


def _map_rows(
    directory: Path,
    project: str,
    data_roots: dict,
    user_project_config: dict | None,
) -> list[list]:
    matching_roots = []
    for root in data_roots:
        resolved_root = Path(root).resolve()
        try:
            directory.relative_to(resolved_root)
        except ValueError:
            continue
        matching_roots.append(resolved_root)

    if not matching_roots:
        raise ValueError(
            f"Dataset directory {directory} is not under any configured data_roots path"
        )

    expected_facets = _project_drs(project, user_project_config)
    if len(directory.parts) < len(expected_facets) + 1:
        raise ValueError(
            f"Expected {len(expected_facets)} DRS directories before the version "
            f"in {directory}"
        )

    drs_parts = directory.parts[-(len(expected_facets) + 1):-1]
    version_directory = directory.name
    if not version_directory.startswith("v") or len(version_directory) == 1:
        raise ValueError(
            f"Dataset directory must be a version directory named v<version>: {directory}"
        )
    version = version_directory[1:]
    dataset_version_id = f"{'.'.join(drs_parts)}#{version}"

    netcdf_files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in {".nc", ".nc4", ".cdf"}
    )
    if not netcdf_files:
        raise ValueError(f"No NetCDF files found in {directory}")

    rows = []
    for path in netcdf_files:
        stat = path.stat()
        digest = sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        rows.append(
            [
                dataset_version_id,
                str(path.resolve()),
                stat.st_size,
                f"mod_time={stat.st_mtime}",
                f"checksum={digest.hexdigest()}",
                "checksum_type=SHA256",
            ]
        )
    return rows


def stac_item_from_directory(
    directory: str | Path,
    project: str,
    data_node: str,
    data_roots: dict,
    *,
    stac_config: dict | None = None,
    user_project_config: dict | None = None,
    index_node: str = "",
    test: bool = False,
    disable_citation: bool = False,
) -> dict:
    """Scan a dataset version directory and return its ESGF-style STAC Item.

    The directory must be the version level of a DRS tree under a configured
    ``data_roots`` path. Files are scanned as NetCDF and their SHA-256 checksums
    are calculated for the STAC file assets.
    """

    dataset_directory = Path(directory).expanduser().resolve()
    if not dataset_directory.is_dir():
        raise NotADirectoryError(f"Dataset directory not found: {dataset_directory}")
    if not data_roots:
        raise ValueError("At least one data_roots mapping is required")

    normalized_roots = {
        str(Path(root).expanduser().resolve()): mapped_root
        for root, mapped_root in sorted(
            data_roots.items(),
            key=lambda entry: len(Path(entry[0]).expanduser().parts),
        )
    }
    map_rows = _map_rows(
        dataset_directory, project, normalized_roots, user_project_config
    )
    scan_handler = ESGPubNC4Handler(log.return_logger("STAC preview scan"))
    scan_result = scan_handler.nc4_load(map_rows)
    dataset_builder = ESGPubMakeDataset(
        data_node,
        index_node,
        False,
        "none",
        normalized_roots,
        None,
        ESGPubNC4Handler,
    )
    dataset_builder.set_project(project)
    records = dataset_builder.get_records(
        map_rows, scan_result, user_project=user_project_config
    )

    project_family = project.lower()
    pid_citation = ESGPubPidCite(
        records,
        {},
        data_node,
        test=test,
        project_family=project_family,
        disable_cite=disable_citation,
    )
    if project_family in PID_PREFIX:
        dataset_pid = pid_citation.gen_pid(records[-1]["instance_id"])
        citation_url = pid_citation.citation_url()
        for record in records:
            record["pid"] = dataset_pid
            if citation_url:
                record["citation_url"] = citation_url

    item = ESGSTACConverter(stac_config or {}).convert2stac(records)
    if item is None:
        raise ValueError(f"Could not create a STAC Item for {dataset_directory}")
    return item