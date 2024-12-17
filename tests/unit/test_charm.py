# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing
"""Unit tests for PrometheusJujuExporterCharm."""
import pathlib
from base64 import b64decode
from itertools import repeat
from unittest import mock

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus
from packaging import version

import charm
import exporter


@pytest.mark.parametrize(
    "event_name, handler",
    [
        ("on.config_changed", "_on_config_changed"),
        ("on.install", "_on_install"),
        ("prometheus_target.on.prometheus_available", "_on_prometheus_available"),
        ("on.upgrade_charm", "_on_upgrade_charm"),
        ("on.update_status", "_on_update_status"),
        ("on.stop", "_on_stop"),
    ],
)
def test_charm_event_mapping(event_name, handler, harness, mocker):
    """Test that all events are bound to the expected event handlers."""
    mocked_handler = mocker.patch.object(harness.charm, handler)

    event = harness.charm
    for object_ in event_name.split("."):
        event = getattr(event, object_)

    event.emit()

    mocked_handler.assert_called_once()


@pytest.mark.parametrize(
    "resource_exists, resource_size, is_path_expected",
    [
        (False, 0, False),  # In case resource was not attached, return None
        (True, 0, False),  # In case the attached resource is empty file, return None
        (True, 10, True),  # If resource is attached and has size, return local path
    ],
)
def test_snap_path_property(resource_exists, resource_size, is_path_expected, harness):
    """Test that 'snap_path' property returns file path only when real resource is attached.

    If resource is not attached or if it's an empty file, this property should return None.
    """
    snap_name = "exporter-snap"
    if resource_exists:
        # Generate some fake data for snap file if it's supposed to have some
        snap_data = "".join(list(repeat("0", resource_size)))
        harness.add_resource(snap_name, snap_data)

    expected_path = (
        str(harness.charm.model.resources.fetch(snap_name)) if is_path_expected else None
    )

    assert harness.charm.snap_path == expected_path


@pytest.mark.parametrize(
    "controller_version, channel",
    [
        ("2.6.5", "2.8/stable"),  # In case controller version is 2.6.x, return 2.8/stable
        ("2.7.6", "2.8/stable"),  # In case controller version is 2.7.x, return 2.8/stable
        ("2.8.8", "2.8/stable"),  # In case controller version is 2.8.x, return 2.8/stable
        ("2.9.42.2", "2.9/stable"),  # In case controller version is 2.9.x, return 2.9/stable
        ("3.1.5", "3/stable"),  # In case controller version is 3.1.5, return 3/stable
        ("3.2.5", "3/stable"),  # In case controller version is 3.2.5, return 3/stable
        ("3.3.4", "3/stable"),  # In case controller version is 3.3.4, return 3/stable
        ("3.4.1", "3/stable"),  # In case controller version is 3.4.1, return 3/stable
        ("3.6.0", "3/stable"),  # In case controller version is 3.6.0, return 3/stable
    ],
)
def test_snap_channel_property(controller_version, channel, harness, mocker):
    """Test that 'snap_channel' property returns the correct channel."""
    mocker.patch.object(
        harness.charm, "get_controller_version", return_value=version.parse(controller_version)
    )

    assert harness.charm.snap_channel == channel


@pytest.mark.parametrize(
    "controller_version",
    [
        "2.5.5",  # Controller version too low
        "4.0.1",  # Controller version too high
    ],
)
def test_snap_channel_property_incompatible_controller(controller_version, harness, mocker):
    """Test that 'snap_channel' property raises exception for incompatible controller version."""
    mocker.patch.object(
        harness.charm, "get_controller_version", return_value=version.parse(controller_version)
    )

    with pytest.raises(charm.ControllerIncompatibleError):
        harness.charm.snap_channel


def test_get_controller_version_success(harness, mocker):
    """Test successfully parsing controller version data out of an agent.conf file."""
    charm_path = "/var/lib/juju/agents/unit-0/charm/"
    agent_config_path = pathlib.Path(charm_path).joinpath("../agent.conf")
    agent_conf_data = {"upgradedToVersion": "2.9.42.2"}
    agent_conf_content = yaml.safe_dump(agent_conf_data, indent=2)
    mocker.patch.object(charm.hookenv, "charm_dir", return_value=charm_path)

    with mock.patch("builtins.open", mock.mock_open(read_data=agent_conf_content)) as open_mock:
        expected_controller_version = version.parse(agent_conf_data["upgradedToVersion"])
        controller_version = harness.charm.get_controller_version()
        assert controller_version == expected_controller_version

    open_mock.assert_called_once_with(agent_config_path, "r", encoding="utf-8")


def test_get_controller_version_fail(harness, mocker):
    """Test failure when controller version can't be parsed out of an agent.conf file."""
    charm_path = "/var/lib/juju/agents/unit-0/charm/"
    agent_config_path = pathlib.Path(charm_path).joinpath("../agent.conf")
    agent_conf_data = {}
    agent_conf_content = yaml.safe_dump(agent_conf_data, indent=2)
    mocker.patch.object(charm.hookenv, "charm_dir", return_value=charm_path)

    with mock.patch("builtins.open", mock.mock_open(read_data=agent_conf_content)) as open_mock:
        with pytest.raises(RuntimeError):
            harness.charm.get_controller_version()

    open_mock.assert_called_once_with(agent_config_path, "r", encoding="utf-8")


def test_get_controller_ca_cert_from_file_success(harness, mocker):
    """Test successfully parsing CA cert data out of an agent.conf file."""
    charm_path = "/var/lib/juju/agents/unit-0/charm/"
    agent_config_path = pathlib.Path(charm_path).joinpath("../agent.conf")
    agent_conf_data = {"cacert": "CA DATA"}
    agent_conf_content = yaml.safe_dump(agent_conf_data, indent=2)
    mocker.patch.object(charm.hookenv, "charm_dir", return_value=charm_path)

    with mock.patch("builtins.open", mock.mock_open(read_data=agent_conf_content)) as open_mock:
        expected_ca_cert = agent_conf_data["cacert"]
        ca_cert = harness.charm.get_controller_ca_cert()
        assert ca_cert == expected_ca_cert

    open_mock.assert_called_once_with(agent_config_path, "r", encoding="utf-8")


def test_get_controller_ca_cert_from_file_fail(harness, mocker):
    """Test failure when CA cert can't be parsed out of an agent.conf file."""
    charm_path = "/var/lib/juju/agents/unit-0/charm/"
    agent_config_path = pathlib.Path(charm_path).joinpath("../agent.conf")
    agent_conf_data = {}
    agent_conf_content = yaml.safe_dump(agent_conf_data, indent=2)
    mocker.patch.object(charm.hookenv, "charm_dir", return_value=charm_path)

    with mock.patch("builtins.open", mock.mock_open(read_data=agent_conf_content)) as open_mock:
        with pytest.raises(RuntimeError):
            harness.charm.get_controller_ca_cert()

    open_mock.assert_called_once_with(agent_config_path, "r", encoding="utf-8")


def test_get_controller_ca_cert_from_config_success(harness):
    """Test successfully parsing CA certificate from config option."""
    ca_data = "VGhpcyBpcyB2YWxpZCBDQQ=="
    with harness.hooks_disabled():
        harness.update_config({"controller-ca-cert": ca_data})

    expected_data = b64decode(ca_data).decode(encoding="ascii")
    assert expected_data == harness.charm.get_controller_ca_cert()


def test_get_controller_ca_cert_from_config_fail(harness):
    """Test failure when parsing CA certificate from config option."""
    ca_data = "this_is-not valid b64"
    with harness.hooks_disabled():
        harness.update_config({"controller-ca-cert": ca_data})

    with pytest.raises(RuntimeError):
        harness.charm.get_controller_ca_cert()


def test_generate_exporter_config_complete(harness, mocker):
    """Test generating complete config file for exporter snap."""
    port = 5000
    controller = "juju-controller:17070"
    expected_controller = [controller]
    customer = "Test Org"
    cloud = "Test cloud"
    ca_cert = "--- CA CERT DATA ---"
    user = "foo"
    password = "bar"
    interval = 5
    debug = False
    prefixes = "TTT:TTT:TTT,FFF:FFF:FFF"
    match_interfaces = r"^(en[os]|eth)\d+|enp\d+s\d+|enx[0-9a-f]+"
    mocker.patch.object(harness.charm, "get_controller_ca_cert", return_value=ca_cert)
    mocker.patch.object(
        exporter.ExporterConfig,
        "controller_endpoint",
        mock.PropertyMock(return_value=expected_controller),
    )

    expected_snap_config = {
        "debug": debug,
        "customer": {
            "name": customer,
            "cloud_name": cloud,
        },
        "exporter": {
            "collect_interval": interval,
            "port": port,
        },
        "juju": {
            "controller_endpoint": expected_controller,
            "password": password,
            "username": user,
            "controller_cacert": ca_cert,
        },
        "detection": {
            "virt_macs": prefixes.split(","),
            "match_interfaces": match_interfaces,
        },
    }

    with harness.hooks_disabled():
        harness.update_config(
            {
                "debug": debug,
                "customer": customer,
                "cloud-name": cloud,
                "controller-url": controller,
                "juju-user": user,
                "juju-password": password,
                "scrape-interval": interval,
                "scrape-port": port,
                "virtual-macs": prefixes,
                "match-interfaces": match_interfaces,
            }
        )

    snap_config = harness.charm.generate_exporter_config()

    assert snap_config == expected_snap_config


def test_generate_exporter_config_incomplete(harness, mocker):
    """Test that generated config will have 'None' values for missing config options."""
    expected_missing_config = {"juju": ["controller_endpoint", "username", "password"]}
    expected_present_config = {"exporter": ["collect_interval", "port"]}
    mocker.patch.object(harness.charm, "get_controller_ca_cert", return_value="ca")

    with harness.hooks_disabled():
        harness.update_config(
            {
                "controller-url": "",
                "juju-user": "",
                "juju-password": "",
                "scrape-interval": 5,
                "scrape-port": 5000,
                "virtual-macs": "FFF:FFF:FFF",
            }
        )

    snap_config = harness.charm.generate_exporter_config()

    for section, missing_keys in expected_missing_config.items():
        for key in missing_keys:
            assert not snap_config[section][key]

    for section, present_keys in expected_present_config.items():
        for key in present_keys:
            assert snap_config[section][key]


def test_reconfigure_scrape_target_success(harness, mocker):
    """Test updating scrape target of Prometheus successfully."""
    port = 5000
    interval_min = 5
    interval_sec = interval_min * 60
    timeout = 30
    expose_target_mock = mocker.patch.object(
        harness.charm.prometheus_target, "expose_scrape_target"
    )

    with harness.hooks_disabled():
        harness.update_config(
            {"scrape-port": port, "scrape-interval": interval_min, "scrape-timeout": timeout}
        )

    harness.charm.reconfigure_scrape_target()
    expose_target_mock.assert_called_once_with(
        port, "/metrics", scrape_interval=f"{interval_sec}s", scrape_timeout=f"{timeout}s"
    )


def test_reconfigure_scrape_target_fail(harness, mocker):
    """Test failure when updating scrape target of Prometheus."""
    expose_target_mock = mocker.patch.object(
        harness.charm.prometheus_target, "expose_scrape_target"
    )
    logger_mock = mocker.patch.object(charm, "logger")

    # re-raise error in case the prometheus target configuration fails
    exception = charm.PrometheusConfigError()
    expose_target_mock.side_effect = exception
    with pytest.raises(charm.PrometheusConfigError):
        harness.charm.reconfigure_scrape_target()

    logger_mock.error.assert_called_once_with(
        "Failed to configure prometheus scrape target: %s", exception
    )


def test_reconfigure_open_ports(harness, mocker):
    """Test updating which ports are open on units."""
    old_port_spec = "5000/tcp"
    old_port, old_protocol = old_port_spec.split("/")
    new_port = 6000

    mocker.patch.object(charm.hookenv, "opened_ports", return_value=[old_port_spec])
    mock_open_port = mocker.patch.object(charm.hookenv, "open_port")
    mock_close_port = mocker.patch.object(charm.hookenv, "close_port")

    with harness.hooks_disabled():
        harness.update_config({"scrape-port": new_port})

    harness.charm.reconfigure_open_ports()

    mock_close_port.assert_called_once_with(old_port, old_protocol)
    mock_open_port.assert_called_once_with(new_port)


def test_on_install_callback_success(harness, mocker):
    """Test handling of InstallEvent with '_on_install' callback."""
    exporter_install = mocker.patch.object(harness.charm.exporter, "install")
    mocker.patch.object(
        harness.charm, "get_controller_version", return_value=version.parse("2.9.42.2")
    )
    harness.charm._on_install(None)
    exporter_install.assert_called_once_with(harness.charm.snap_path, harness.charm.snap_channel)
    assert isinstance(harness.charm.unit.status, charm.MaintenanceStatus)


def test_on_upgrade_charm(harness, mocker):
    """Test event handler for charm upgrade.

    This event should trigger re-installation of snap and re-rendering of snap config.
    """
    on_install_mock = mocker.patch.object(harness.charm, "_on_install")
    on_config_mock = mocker.patch.object(harness.charm, "_on_config_changed")

    harness.charm._on_upgrade_charm(None)

    on_install_mock.assert_called_once_with(None)
    on_config_mock.assert_called_once_with(None)


def test_on_stop(harness, mocker):
    """Test that charm cleans up exporter snap when it stops."""
    uninstall_mock = mocker.patch.object(harness.charm.exporter, "uninstall")

    harness.charm._on_stop(None)

    uninstall_mock.assert_called_once()


def test_on_install_callback_fail(harness, mocker):
    """Test handling of error during InstallEvent."""
    snap_exception = charm.snap.CouldNotAcquireLockException
    mocker.patch.object(
        harness.charm, "get_controller_version", return_value=version.parse("2.9.42.2")
    )
    exporter_install = mocker.patch.object(harness.charm.exporter, "install")

    exporter_install.side_effect = snap_exception
    with pytest.raises(snap_exception):
        harness.charm._on_install(None)


def test_on_config_changed_incomplete(harness, mocker):
    """Test what happens when charm has incomplete configuration."""
    incomplete_config = {}
    mocker.patch.object(harness.charm, "generate_exporter_config", return_value=incomplete_config)
    mock_apply_config = mocker.patch.object(
        harness.charm.exporter, "apply_config", side_effect=charm.ExporterConfigError
    )

    harness.charm._on_config_changed(None)

    mock_apply_config.assert_called_once_with(incomplete_config)
    assert isinstance(harness.charm.unit.status, charm.BlockedStatus)


def test_on_config_changed_success(mocker, harness):
    """Test successful application of new config values."""
    valid_config = {"valid": "config"}
    mocker.patch.object(harness.charm, "generate_exporter_config", return_value=valid_config)
    mocker.patch.object(exporter.ExporterSnap, "is_running", return_value=True)
    mock_apply_config = mocker.patch.object(harness.charm.exporter, "apply_config")
    mock_reconfigure_scrape = mocker.patch.object(harness.charm, "reconfigure_scrape_target")
    mock_reconfigure_ports = mocker.patch.object(harness.charm, "reconfigure_open_ports")

    harness.charm._on_config_changed(None)

    mock_apply_config.assert_called_once_with(valid_config)
    mock_reconfigure_scrape.assert_called_once_with()
    mock_reconfigure_ports.assert_called_once_with()

    assert isinstance(harness.charm.unit.status, charm.ActiveStatus)


def test_on_prometheus_available(harness, mocker):
    """Test that handler for 'prometheus_available' reconfigures scrape target."""
    mock_reconfigure = mocker.patch.object(harness.charm, "reconfigure_scrape_target")

    harness.charm._on_prometheus_available(None)

    mock_reconfigure.assert_called_once_with()


@pytest.mark.parametrize(
    "current_status, service_running, expected_status",
    [
        (ActiveStatus, True, ActiveStatus),
        (ActiveStatus, False, BlockedStatus),
        (BlockedStatus, False, BlockedStatus),
        (BlockedStatus, True, ActiveStatus),
    ],
)
def test_evaluate_status(current_status, service_running, expected_status, harness, mocker):
    """Test that wrapper that evaluates final unit status sets correct workload status.

    Expected behavior:
    <Current Status>  <Is exporter running>  <Final Status>
    Active              Yes                     Active
    Active              No                      Blocked
    Blocked             Yes                     Active
    Blocked             No                      Blocked
    """
    mocker.patch.object(exporter.ExporterSnap, "is_running", return_value=service_running)
    harness.charm.unit.status = current_status("Initial status")

    # trigger actual status evaluation wrapper via update-status event
    harness.charm._on_update_status(None)

    assert isinstance(harness.charm.unit.status, expected_status)
