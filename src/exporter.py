#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Exporter snap helper.

Module focused on handling operations related to prometheus-juju-exporter snap.
"""
import logging
import os
import subprocess
from typing import Any, Dict, List, NamedTuple, Optional, Union

import yaml
from charmhelpers.core import host as ch_host
from charmhelpers.fetch import snap
from packaging import version

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class ExporterConfigError(Exception):
    """Indicates problem with configuration of exporter service."""


class ExporterSnapError(Exception):
    """Indicates problem with exporter snap."""


class ExporterConfig(NamedTuple):
    """Data class that holds information required for exporter configuration."""

    debug: Optional[str] = None
    customer: Optional[str] = None
    cloud: Optional[str] = None
    controller: Optional[str] = None
    ca_cert: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    interval: Optional[str] = None
    port: Optional[str] = None
    prefixes: Optional[str] = None
    match_interfaces: Optional[str] = None

    @property
    def controller_endpoint(self) -> Union[str, List[str]]:
        """Property that renders value for 'juju.controller_endpoint' option.

        Output is determined based on currently installed snap. Only
        prometheus-juju-exporter > 1.0.1 can accept list of strings in this config option.
        """
        if self.controller is None or self.controller == "":
            return ""

        endpoints: Union[str, List[str]] = self.controller.split(",")
        current_version = ExporterSnap.version()

        if current_version <= version.Version("1.0.1"):
            if len(endpoints) > 1:
                raise ExporterConfigError(
                    f"Currently installed version of exporter ({current_version}) does "
                    f"not support HA controller configuration."
                )
            endpoints = endpoints[0]

        return endpoints

    def render(self) -> Dict[str, Union[Dict[str, Union[List[str], str, None]], str, None]]:
        """Return dict that can be written to an exporter config file as a yaml."""
        return {
            "debug": self.debug,
            "customer": {
                "name": self.customer,
                "cloud_name": self.cloud,
            },
            "juju": {
                "controller_endpoint": self.controller_endpoint,
                "controller_cacert": self.ca_cert,
                "username": self.user,
                "password": self.password,
            },
            "exporter": {
                "collect_interval": self.interval,
                "port": self.port,
            },
            "detection": {
                "virt_macs": self.prefixes.split(",") if self.prefixes else [],
                "match_interfaces": self.match_interfaces or ".*",
            },
        }


class ExporterSnap:
    """Class that handles operations of prometheus-juju-exporter snap and related services."""

    SNAP_NAME = "prometheus-juju-exporter"
    SNAP_CONFIG_PATH = f"/var/snap/{SNAP_NAME}/current/config.yaml"
    _SNAP_ACTIONS = [
        "stop",
        "start",
        "restart",
    ]
    _REQUIRED_CONFIG = [
        "customer.name",
        "customer.cloud_name",
        "juju.controller_endpoint",
        "juju.controller_cacert",
        "juju.username",
        "juju.password",
        "exporter.port",
        "exporter.collect_interval",
        "detection.virt_macs",
        "detection.match_interfaces",
    ]

    @property
    def service_name(self) -> str:
        """Return name of the exporter's systemd service."""
        return f"snap.{self.SNAP_NAME}.{self.SNAP_NAME}.service"

    def install(
        self, snap_path: Optional[str] = None, snap_channel: str = "latest/stable"
    ) -> None:
        """Install prometheus-juju-exporter snap.

        This method tries to install snap from local file if parameter :snap_path is provided.
        Otherwise, it'll attempt installation from snap store based on ExporterSnap.SNAP_NAME.

        :param snap_path: Optional parameter to provide local file as source of snap installation.
        :raises:
            snap.CouldNotAcquireLockException: In case of snap installation failure.
        """
        if snap_path:
            logger.info("Installing snap %s from local resource.", self.SNAP_NAME)
            snap.snap_install(snap_path, "--dangerous")
        else:
            logger.info("Installing %s snap from snap store.", self.SNAP_NAME)
            snap.snap_install(self.SNAP_NAME, "--channel", snap_channel)

    def uninstall(self) -> None:
        """Remove prometheus-juju-exporter snap."""
        snap.snap_remove(self.SNAP_NAME)

    def _validate_required_options(self, config: Dict[str, Any]) -> List[str]:
        """Validate that config has all required options for snap to run."""
        missing_options = []
        for option in self._REQUIRED_CONFIG:
            config_value = config
            for identifier in option.split("."):
                config_value = config_value.get(identifier, {})
            if not config_value:
                missing_options.append(option)

        return missing_options

    @staticmethod
    def _validate_option_values(config: Dict[str, Any]) -> str:
        """Validate sane values for some of the config parameters where its feasible."""
        errors = ""

        # Verify that 'port' is number within valid port range.
        try:
            port = int(config["exporter"]["port"])
            if not 0 < port < 65535:
                errors += f"Port {port} is not valid port number.{os.linesep}"
        except ValueError:
            errors += f"Configuration option 'port' must be a number.{os.linesep}"
        except KeyError:
            pass  # Options was not in the config

        # Verify that 'collect_interval' is positive number.
        try:
            collect_interval = int(config["exporter"]["collect_interval"])
            if collect_interval < 1:
                errors += (
                    f"Configuration option 'collect_interval' must be a "
                    f"positive number.{os.linesep}"
                )
        except ValueError:
            errors += f"Configuration option 'collect_interval' must be a number.{os.linesep}"
        except KeyError:
            pass  # Options was not in the config

        return errors

    def validate_config(self, config: Dict[str, Any]) -> None:
        """Validate supplied config file for exporter service.

        :param config: config dictionary to be validated
        :raises:
            ExporterConfigError: In case the config does not pass the validation process. For
                example if the required fields are missing or values have unexpected format.
        """
        errors = ""

        missing_options = self._validate_required_options(config)
        if missing_options:
            missing_str = ", ".join(missing_options)
            errors += f"Following config options are missing: {missing_str}{os.linesep}"

        errors += self._validate_option_values(config)

        if errors:
            raise ExporterConfigError(errors)

    def apply_config(self, exporter_config: Dict[str, Any]) -> None:
        """Update configuration file for exporter service."""
        self.stop()
        logger.info("Updating exporter service configuration.")
        self.validate_config(exporter_config)

        with open(self.SNAP_CONFIG_PATH, "w", encoding="utf-8") as config_file:
            yaml.safe_dump(exporter_config, config_file)
        os.chmod(self.SNAP_CONFIG_PATH, 0o600)

        self.restart()
        logger.info("Exporter configuration updated.")

    @classmethod
    def version(cls) -> version.Version:
        """Return version of currently installed exporter."""
        cmd = ["snap", "info", cls.SNAP_NAME]
        try:
            raw_output = subprocess.check_output(cmd)
            snap_info = yaml.safe_load(raw_output)
        except (subprocess.CalledProcessError, yaml.YAMLError) as exc:
            raise ExporterSnapError(f"Failed to get exporter snap version: {exc}") from exc

        if "installed" not in snap_info:
            raise ExporterSnapError("Exporter snap is not installed.")

        snap_version = snap_info["installed"].split()[0]
        return version.Version(snap_version)

    def restart(self) -> None:
        """Restart exporter service."""
        self._execute_service_action("restart")

    def stop(self) -> None:
        """Stop exporter service."""
        self._execute_service_action("stop")

    def start(self) -> None:
        """Start exporter service."""
        self._execute_service_action("start")

    def is_running(self) -> bool:
        """Check if exporter service is running."""
        return ch_host.service_running(self.service_name)

    def _execute_service_action(self, action: str) -> None:
        """Execute one of the supported snap service actions.

        Supported actions:
            - stop
            - start
            - restart

        :param action: snap service action to execute
        :raises:
            RuntimeError: If requested action is not supported.
        """
        if action not in self._SNAP_ACTIONS:
            raise RuntimeError(f"Snap service action '{action}' is not supported.")
        logger.info("%s service executing action: %s", self.SNAP_NAME, action)
        subprocess.call(["snap", action, self.SNAP_NAME])
