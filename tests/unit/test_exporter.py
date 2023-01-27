# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
"""Unit tests for helper class ExporterSnap that handles actions related to the exporter snap."""
from typing import Dict
from unittest.mock import ANY, mock_open, patch

import pytest

import exporter


def validate_config_error(config: Dict, expected_error: str):
    """Run config validation and verify that expected error is present in the raised exception."""
    exporter_ = exporter.ExporterSnap()
    with pytest.raises(exporter.ExporterConfigError, match=expected_error):
        exporter_.validate_config(config)


@pytest.mark.parametrize("local_snap", [True, False])
def test_exporter_snap_install(local_snap, mocker):
    """Test method that install exporter snap from local file or from snap store."""
    snap_path = "/tmp/path/snap" if local_snap else None
    mock_snap_install = mocker.patch.object(exporter.snap, "snap_install")

    exporter_ = exporter.ExporterSnap()

    exporter_.install(snap_path)

    if local_snap:
        mock_snap_install.assert_called_once_with(snap_path, "--dangerous")
    else:
        mock_snap_install.assert_called_once_with(exporter_.SNAP_NAME)


def test_validate_config_missing_fields():
    """Test config validation with all required fields missing."""
    missing_options = ", ".join(exporter.ExporterSnap._REQUIRED_CONFIG)
    expected_err = f"Following config options are missing: {missing_options}"

    validate_config_error({}, expected_err)


def test_validate_config_port_not_number():
    """Test config validation when port is not defined as number."""
    config = {"exporter": {"port": "foo"}}
    expected_err = "Configuration option 'port' must be a number."

    validate_config_error(config, expected_err)


@pytest.mark.parametrize(
    "port",
    [
        0,  # too low
        65536,  # too high
    ],
)
def test_validate_config_port_out_of_range(port):
    """Test config validation when port is defined out of allowed range."""
    expected_error = f"Port {port} is not valid port number."
    validate_config_error({"exporter": {"port": port}}, expected_error)


def test_validate_configrefresh_not_number():
    """Test config validation when 'refresh' option is not a number."""
    expected_err = "Configuration option 'collect_interval' must be a number."
    validate_config_error({"exporter": {"collect_interval": "foo"}}, expected_err)


def test_validate_config_refresh_below_zero():
    """Test config validation when 'refresh' option is less than 1."""
    expected_err = "Configuration option 'collect_interval' must be a positive number."
    validate_config_error({"exporter": {"collect_interval": 0}}, expected_err)


def test_validate_config():
    """Test positively validating snap exporter config."""
    config = {
        "customer": {"name": "Test Org", "cloud_name": "Test Cloud"},
        "exporter": {
            "port": 5000,
            "collect_interval": 5,
        },
        "juju": {
            "controller_endpoint": "10.0.0.99:17070",
            "controller_cacert": "CA CERT DATA",
            "username": "foo",
            "password": "bar",
        },
        "detection": {
            "virt_macs": "FFF:FFF:FFF",
        },
    }

    exporter_ = exporter.ExporterSnap()

    try:
        exporter_.validate_config(config)
    except exporter.ExporterConfigError:
        pytest.fail("Configuration expected to pass but did not.")


def test_apply_config_success(mocker):
    """Test successfully applying snap configuration."""
    mock_stop = mocker.patch.object(exporter.ExporterSnap, "stop")
    mock_start = mocker.patch.object(exporter.ExporterSnap, "start")
    mock_dump = mocker.patch.object(exporter.yaml, "safe_dump")
    mock_validate = mocker.patch.object(exporter.ExporterSnap, "validate_config")
    config = {"valid": "config"}
    exporter_ = exporter.ExporterSnap()

    with patch("builtins.open", new_callable=mock_open) as file_:
        exporter_.apply_config(config)

        mock_stop.assert_called_once_with()
        mock_validate.assert_called_once_with(config)
        mock_dump.assert_called_once_with(config, ANY)
        file_.assert_called_once_with(exporter_.SNAP_CONFIG_PATH, "w", encoding="utf-8")
        mock_start.assert_called_once_with()


def test_apply_config_fail(mocker):
    """Test failure to apply snap configuration.

    In case of failure, old config should not be overwritten
    and the service should remain stopped.
    """
    mock_stop = mocker.patch.object(exporter.ExporterSnap, "stop")
    mock_start = mocker.patch.object(exporter.ExporterSnap, "start")
    mock_dump = mocker.patch.object(exporter.yaml, "safe_dump")
    mock_validate = mocker.patch.object(exporter.ExporterSnap, "validate_config")
    config = {}
    exporter_ = exporter.ExporterSnap()

    mock_validate.side_effect = exporter.ExporterConfigError
    with patch("builtins.open", new_callable=mock_open):
        with pytest.raises(exporter.ExporterConfigError):
            exporter_.apply_config(config)

    mock_stop.assert_called_once_with()
    mock_validate.assert_called_once_with(config)
    mock_dump.assert_not_called()
    mock_start.assert_not_called()


@pytest.mark.parametrize(
    "action",
    [
        "start",
        "stop",
        "restart",
    ],
)
def test_exporter_service_actions(action, mocker):
    """Test running available actions on exporter service."""
    mock_action = mocker.patch.object(exporter.ExporterSnap, "_execute_service_action")

    exporter_ = exporter.ExporterSnap()
    getattr(exporter_, action)()

    mock_action.assert_called_once_with(action)


def test_execute_service_action(mocker):
    """Test internal method that executes snap service actions."""
    mock_call = mocker.patch.object(exporter.subprocess, "call")
    action = "restart"
    exporter_ = exporter.ExporterSnap()
    expected_command = ["snap", action, exporter_.SNAP_NAME]

    exporter_._execute_service_action(action)

    mock_call.assert_called_once_with(expected_command)


def test_execute_service_action_unknown(mocker):
    """Test that '_execute_service_action' raises error if it does not recognize the action."""
    mock_call = mocker.patch.object(exporter.subprocess, "call")
    bad_action = "foo"

    exporter_ = exporter.ExporterSnap()
    with pytest.raises(RuntimeError):
        exporter_._execute_service_action(bad_action)

    mock_call.assert_not_called()


def test_service_name():
    """Test that `service_name` property returns expected value."""
    exporter_ = exporter.ExporterSnap()
    expected_service = f"snap.{exporter_.SNAP_NAME}.{exporter_.SNAP_NAME}.service"

    assert exporter_.service_name == expected_service


@pytest.mark.parametrize("running", [True, False])
def test_exporter_service_running(running, mocker):
    """Test that `is_running` method returns True/False based on service status."""
    mock_service_running = mocker.patch.object(
        exporter.ch_host, "service_running", return_value=running
    )

    exporter_ = exporter.ExporterSnap()

    assert exporter_.is_running() == running
    mock_service_running.assert_called_once_with(exporter_.service_name)
