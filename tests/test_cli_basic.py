"""Basic CLI tests to verify all commands work after reorganization."""
import subprocess
import sys
import pytest

from esgcet.cli import esgstac as stac_cli
from esgcet.stac import stac_preview

CLI_COMMANDS = [
    'esgpublish',
    'esgindexpub',
    'esgupdate',
    'esgunpublish',
    'esgmapconv',
    'esgpidcitepub',
    'esglogin',
    'esgmkpubrec',
    'esgadd',
    'esgstac',
    'esgcet',
]


@pytest.mark.parametrize("command", CLI_COMMANDS)
def test_cli_help(command):
    """Test that all CLI commands can show help."""
    result = subprocess.run([command, '--help'], capture_output=True, text=True)
    assert result.returncode == 0, f"{command} --help failed with: {result.stderr}"


@pytest.mark.parametrize("command", CLI_COMMANDS)
def test_cli_version(command):
    """Test that all CLI commands can show version (if supported)."""
    result = subprocess.run([command, '--version'], capture_output=True, text=True)
    # Some commands may not have --version, so we just check it doesn't crash badly
    # Return codes: 0 = success, 2 = argparse error (no --version flag)
    assert result.returncode in [0, 2], f"{command} --version crashed with code {result.returncode}: {result.stderr}"


def test_validate_stac_item_uses_referenced_schema(monkeypatch):
    schema_url = "https://example.test/stac-transaction-api/cmip6/schema.json"
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return schema

    requested_urls = []

    def fake_get(url, timeout):
        requested_urls.append((url, timeout))
        return FakeResponse()

    monkeypatch.setattr(stac_preview.requests, "get", fake_get)
    errors = stac_preview.validate_stac_item(
        {"id": "dataset", "stac_extensions": [schema_url]}
    )

    assert requested_urls == [(schema_url, 30)]
    assert errors == ["$.id: 'dataset' is not of type 'integer'"]


@pytest.mark.parametrize(
    ("validation_errors", "expected_status", "expected_output"),
    [
        ([], 0, "PASS\n"),
        (["$.id: missing required property"], 1, "FAIL\n- $.id: missing required property\n"),
    ],
)
def test_esgstac_valid_flag_reports_result(
    monkeypatch, tmp_path, capsys, validation_errors, expected_status, expected_output
):
    config = tmp_path / "config.yaml"
    config.write_text(
        "project: CMIP6\ndata_node: test.node\ndata_roots: {}\ntest: 'false'\ndisable_citation: 'false'\n",
        encoding="utf-8",
    )
    build_options = {}

    def fake_build(*args, **kwargs):
        build_options.update(kwargs)
        return {}

    monkeypatch.setattr(
        sys,
        "argv",
        ["esgstac", str(tmp_path), "--config", str(config), "--valid"],
    )
    monkeypatch.setattr(
        stac_cli, "stac_item_from_directory", fake_build
    )
    monkeypatch.setattr(
        stac_cli, "validate_stac_item", lambda item: validation_errors
    )

    assert stacpreview_cli.main() == expected_status
    assert capsys.readouterr().out == expected_output
    assert build_options["test"] is False
    assert build_options["disable_citation"] is False
