# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
"""Unit tests for helper class ExporterSnap that handles actions related to the exporter snap."""
import subprocess
from typing import Dict
from unittest.mock import ANY, PropertyMock, mock_open, patch

import pytest
import yaml
from packaging import version

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

    exporter_.install(snap_path, "2.9/stable")

    if local_snap:
        mock_snap_install.assert_called_once_with(snap_path, "--dangerous")
    else:
        mock_snap_install.assert_called_once_with(exporter_.SNAP_NAME, "--channel", "2.9/stable")


def test_exporter_snap_uninstall(mocker):
    """Test uninstallation of exporter snap."""
    snap_remove_mock = mocker.patch.object(exporter.snap, "snap_remove")

    exporter_ = exporter.ExporterSnap()
    exporter_.uninstall()

    snap_remove_mock.assert_called_once_with(exporter_.SNAP_NAME)


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
        "debug": False,
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
            "match_interfaces": r"^(en[os]|eth)\d+|enp\d+s\d+|enx[0-9a-f]+",
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
    mock_start = mocker.patch.object(exporter.ExporterSnap, "restart")
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
    mock_start = mocker.patch.object(exporter.ExporterSnap, "restart")
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


def test_exporter_snap_version_success(snap_info_1_0_1, mocker):
    """Test successfully detecting exporter snap version."""
    expected_version = version.parse("1.0.1")
    cmd_output = yaml.dump(snap_info_1_0_1)
    mocker.patch.object(exporter.subprocess, "check_output", return_value=cmd_output)

    assert exporter.ExporterSnap.version() == expected_version


def test_exporter_snap_version_not_installed(snap_info_1_0_1, mocker):
    """Test failure to detect exporter snap version when snap is not installed."""
    snap_info = snap_info_1_0_1.copy()
    snap_info.pop("installed")
    cmd_output = yaml.dump(snap_info)
    mocker.patch.object(exporter.subprocess, "check_output", return_value=cmd_output)

    with pytest.raises(exporter.ExporterSnapError):
        _ = exporter.ExporterSnap.version()


def test_exporter_snap_version_failure(mocker):
    """Test failure to get snap info when detecting exporter snap version."""
    err = subprocess.CalledProcessError(1, "snap info", "Command not found")
    mocker.patch.object(exporter.subprocess, "check_output", side_effect=err)

    with pytest.raises(exporter.ExporterSnapError):
        _ = exporter.ExporterSnap.version()


@pytest.mark.parametrize(
    "exporter_version, expected_value",
    [
        (version.parse("1.0.1"), "10.0.0.1:17070"),
        (version.parse("1.0.2"), ["10.0.0.1:17070"]),
    ],
)
def test_exporter_config_controller_endpoint(exporter_version, expected_value, mocker):
    """Test that ExporterConfig.controller_endpoint returns data in correct format.

    Based on the installed version of snap, this property should return:
    * single string for prometheus-juju-exporter <= 1.0.1
    * list of string for prometheus-juju-exporter > 1.0.1
    """
    mocker.patch.object(exporter.ExporterSnap, "version", return_value=exporter_version)
    raw_controller_endpoint = "10.0.0.1:17070"
    config = exporter.ExporterConfig(controller=raw_controller_endpoint)

    assert config.controller_endpoint == expected_value


def test_exporter_config_controller_endpoint_incompatible(mocker):
    """Test incompatibilities between 'controller_endpoint' value and installed exporter.

    Only prometheus-juju-exporter > 1.0.1 can accept comma-separated list of controller
    endpoints.
    """
    exporter_version = version.parse("1.0.1")
    mocker.patch.object(exporter.ExporterSnap, "version", return_value=exporter_version)
    invalid_endpoints = "10.0.0.1:17070,10.0.0.2:17070"
    config = exporter.ExporterConfig(controller=invalid_endpoints)

    with pytest.raises(exporter.ExporterConfigError):
        _ = config.controller_endpoint


@pytest.mark.parametrize("empty_value", ["", None])
def test_exporter_config_controller_empty_value(empty_value):
    """Test that ExporterConfig.controller_endpoint returns expected empty value."""
    expected_empty_value = ""
    config = exporter.ExporterConfig(controller=empty_value)
    assert config.controller_endpoint == expected_empty_value


def test_exporter_config_render_defaults(mocker):
    """Test that default values get injected to optional config options."""
    customer_name = "Test"
    cloud_name = "Test Cloud"
    controller_endpoint = "10.0.0.1:17070,10.0.0.2:17070"
    expected_controller_endpoint = controller_endpoint.split(",")
    ca_cert = "---BEGIN CERT---\ndata\n---END CERT---"
    username = "admin"
    password = "pass1"
    collection_interval = "1"
    port = "5000"

    default_virt_macs = []
    default_match_interfaces = r".*"
    default_debug = None

    mocker.patch.object(
        exporter.ExporterConfig,
        "controller_endpoint",
        PropertyMock(return_value=expected_controller_endpoint),
    )
    expected_config = {
        "customer": {
            "name": customer_name,
            "cloud_name": cloud_name,
        },
        "juju": {
            "controller_endpoint": expected_controller_endpoint,
            "controller_cacert": ca_cert,
            "username": username,
            "password": password,
        },
        "exporter": {
            "collect_interval": collection_interval,
            "port": port,
        },
        "detection": {
            "virt_macs": default_virt_macs,
            "match_interfaces": default_match_interfaces,
        },
        "debug": default_debug,
    }

    config = exporter.ExporterConfig(
        customer=customer_name,
        cloud=cloud_name,
        controller=controller_endpoint,
        ca_cert=ca_cert,
        user=username,
        password=password,
        interval=collection_interval,
        port=port,
    )

    assert config.render() == expected_config
