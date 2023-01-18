#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
import os
import pathlib
from base64 import b64decode
from binascii import Error as Base64Error
from typing import Any, Dict, Optional

import yaml
from charmhelpers.core import hookenv
from charmhelpers.fetch import snap
from ops.charm import CharmBase, ConfigChangedEvent, InstallEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, ModelError
from prometheus_interface.operator import (
    PrometheusConfigError,
    PrometheusConnected,
    PrometheusScrapeTarget,
)

from exporter import ExporterConfig, ExporterConfigError, ExporterSnap

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class PrometheusJujuExporterCharm(CharmBase):
    """Charm the service."""

    # Mapping between charm and snap configuration options
    SNAP_CONFIG_MAP = {
        "customer": "customer.name",
        "cloud-name": "customer.cloud_name",
        "controller-url": "juju.controller_endpoint",
        "juju-user": "juju.username",
        "juju-password": "juju.password",
        "scrape-interval": "exporter.collect_interval",
        "scrape-port": "exporter.port",
        "virtual-macs": "machine.virt_macs",
    }

    def __init__(self, *args: Any) -> None:
        """Initialize charm."""
        super().__init__(*args)
        self.exporter = ExporterSnap()
        self.prometheus_target = PrometheusScrapeTarget(self, "prometheus-scrape")
        self._snap_path: Optional[str] = None
        self._snap_path_set = False

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(
            self.prometheus_target.on.prometheus_available, self._on_prometheus_available
        )

    @property
    def snap_path(self) -> Optional[str]:
        """Get local path to exporter snap.

        If this charm has snap file for the exporter attached as a resource, this property returns
        path to the snap file. If the resource was not attached of the file is empty, this property
        returns None.
        """
        if not self._snap_path_set:
            try:
                self._snap_path = str(self.model.resources.fetch("exporter-snap"))
                # Don't return path to empty resource file
                if not os.path.getsize(self._snap_path) > 0:
                    self._snap_path = None
            except ModelError:
                self._snap_path = None
            finally:
                self._snap_path_set = True

        return self._snap_path

    def get_controller_ca_cert(self) -> str:
        """Get CA certificate used by targeted Juju controller.

        CA certificate can be directly configured by `controller-ca-cert` option, if it is, the
        value is directly returned by this method. If it is not defined, a CA cert used by the
        controller that deploys this unit will be returned.
        """
        explicit_cert = self.config.get("controller-ca-cert", "")
        if explicit_cert:
            try:
                return b64decode(explicit_cert, validate=True).decode(encoding="ascii")
            except Base64Error as exc:
                logger.error(
                    "Config option 'controller-ca-cert' does not contain valid base64-encoded"
                    " data. Bad data: %s",
                    explicit_cert,
                )
                raise RuntimeError("Invalid base64 value in 'controller-ca-cert' option.") from exc

        agent_conf_path = pathlib.Path(hookenv.charm_dir()).joinpath("../agent.conf")
        with open(agent_conf_path, "r", encoding="utf-8") as conf_file:
            agent_conf = yaml.safe_load(conf_file)

        ca_cert = agent_conf.get("cacert")
        if not ca_cert:
            raise RuntimeError("Charm failed to fetch controller's CA certificate.")

        return ca_cert

    def generate_exporter_config(self) -> Dict[str, Dict[str, Optional[str]]]:
        """Generate exporter service config based on the values from charm config."""
        config = ExporterConfig(
            customer=self.config.get("customer"),
            cloud=self.config.get("cloud-name"),
            controller=self.config.get("controller-url"),
            ca_cert=self.get_controller_ca_cert(),
            user=self.config.get("juju-user"),
            password=self.config.get("juju-password"),
            interval=self.config.get("scrape-interval"),
            port=self.config.get("scrape-port"),
            prefixes=self.config.get("virtual-macs"),
        )

        return config.render()

    def reconfigure_scrape_target(self) -> None:
        """Update scrape target configuration in related Prometheus application.

        Note: this function has no effect if there's no application related via
        'prometheus-scrape'.
        """
        port = self.config["scrape-port"]
        interval_minutes = self.config["scrape-interval"]
        interval = interval_minutes * 60
        timeout = self.config["scrape-timeout"]
        try:
            self.prometheus_target.expose_scrape_target(
                port, "/metrics", scrape_interval=f"{interval}s", scrape_timeout=f"{timeout}s"
            )
        except PrometheusConfigError as exc:
            logger.error("Failed to configure prometheus scrape target: %s", exc)
            raise exc

    def reconfigure_open_ports(self) -> None:
        """Update ports that juju shows as 'opened' in units' status."""
        new_port = self.config["scrape-port"]

        for port_spec in hookenv.opened_ports():
            old_port, protocol = port_spec.split("/")
            logger.debug("Setting port %s as closed.", old_port)
            hookenv.close_port(old_port, protocol)

        logger.debug("Setting port %s as opened.", new_port)
        hookenv.open_port(new_port)

    def _on_install(self, _: InstallEvent) -> None:
        """Install prometheus-juju-exporter snap."""
        self.unit.status = MaintenanceStatus("Installing charm software.")
        try:
            self.exporter.install(self.snap_path)
        except snap.CouldNotAcquireLockException as exc:
            install_source = "local resource" if self.snap_path else "snap store"
            logger.error("Failed to install %s from %s.", self.exporter.SNAP_NAME, install_source)
            raise exc

    def _on_config_changed(self, _: ConfigChangedEvent) -> None:
        """Handle changed configuration."""
        logger.info("Processing new charm configuration.")
        exporter_config = self.generate_exporter_config()
        try:
            self.exporter.apply_config(exporter_config)
        except ExporterConfigError as exc:
            # Replace snap config names with their charm equivalents
            err_msg = str(exc)
            for charm_option, snap_option in self.SNAP_CONFIG_MAP.items():
                err_msg = err_msg.replace(snap_option, charm_option)

            logger.error(err_msg)
            self.unit.status = BlockedStatus("Invalid configuration. Please see logs.")
            return

        self.reconfigure_scrape_target()
        self.reconfigure_open_ports()
        self.unit.status = ActiveStatus("Unit is ready")

    def _on_prometheus_available(self, _: PrometheusConnected) -> None:
        """Trigger configuration of a prometheus scrape target."""
        self.reconfigure_scrape_target()


if __name__ == "__main__":  # pragma: nocover
    main(PrometheusJujuExporterCharm)
