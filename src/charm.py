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
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Union

import yaml
from charmhelpers.core import hookenv
from charmhelpers.fetch import snap
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from ops.charm import (
    CharmBase,
    ConfigChangedEvent,
    InstallEvent,
    StopEvent,
    UpdateStatusEvent,
    UpgradeCharmEvent,
)
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, ModelError
from packaging import version
from prometheus_interface.operator import (
    PrometheusConfigError,
    PrometheusConnected,
    PrometheusScrapeTarget,
)

from exporter import ExporterConfig, ExporterConfigError, ExporterSnap

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class ControllerIncompatibleError(Exception):
    """The version of the current controller is not supported."""


def evaluate_status(func: Callable) -> Callable:
    """Decorate `PrometheusJujuExporterCharm` method to perform status evaluation.

    This wrapper can be used to decorate a method (primarily an event handler) from
    `PrometheusJujuExporterCharm` class. When decorated function is executed, this
    wrapper will perform final assessment of unit's state and sets unit status.
    """

    @wraps(func)
    def wrapper(self: "PrometheusJujuExporterCharm", *args: Any, **kwargs: Any) -> Any:
        """Execute wrapped method and perform status assessment."""
        result = func(self, *args, **kwargs)

        exporter_running = self.exporter.is_running()
        if isinstance(self.unit.status, ActiveStatus) and not exporter_running:
            self.unit.status = BlockedStatus(
                "Exporter service is inactive. (See service logs in unit.)"
            )
        elif not isinstance(self.unit.status, ActiveStatus) and exporter_running:
            self.unit.status = ActiveStatus("Unit is ready")

        return result

    return wrapper


class PrometheusJujuExporterCharm(CharmBase):
    """Charm the service."""

    # Mapping between charm and snap configuration options
    SNAP_CONFIG_MAP = {
        "debug": "debug",
        "customer": "customer.name",
        "cloud-name": "customer.cloud_name",
        "controller-url": "juju.controller_endpoint",
        "juju-user": "juju.username",
        "juju-password": "juju.password",
        "scrape-interval": "exporter.collect_interval",
        "scrape-port": "exporter.port",
        "virtual-macs": "detection.virt_macs",
        "match-interfaces": "detection.match_interfaces",
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
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(
            self.prometheus_target.on.prometheus_available, self._on_prometheus_available
        )

        port = self.config["scrape-port"]
        self.metrics_endpoint = MetricsEndpointProvider(
            self,
            relation_name="prometheus-k8s-scrape",
            jobs=[
                {
                    "static_configs": [{"targets": [f"*:{port}"]}],
                },
            ],
        )
        self.grafana_dashboard_provider = GrafanaDashboardProvider(
            self, relation_name="grafana-k8s-dashboard"
        )
        self.grafana_dashboard_provider._reinitialize_dashboard_data(inject_dropdowns=False)

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

    @property
    def snap_channel(self) -> str:
        """Get the channel for exporter snap.

        The channel is determined by the controller version that the charm is deployed
        under.

        In case the controller version is equal or higher than 2.6 and less than 2.9,
        the snap channel is set to 2.8/stable.

        In case controller's major and minor version match with 2.9, the snap channel
        is set to 2.9/stable.

        In case controller's major and minor version match with 3.x, the snap channel
        is set to 3.x/stable.

        Otherwise, raise ControllerIncompatibleError exception.
        """
        controller_version = self.get_controller_version()

        if controller_version.major == 2:
            if controller_version.minor in [6, 7, 8]:
                return "2.8/stable"
            if controller_version.minor == 9:
                return "2.9/stable"
        elif controller_version.major == 3:
            if controller_version.minor in range(1, 6):
                return "3/stable"

        raise ControllerIncompatibleError(
            f"Juju controller version {str(controller_version)} is not supported. "
            + "Current supported versions are: 2.6, 2.7, 2.8, 2.9, 3.1, 3.2, 3.3, 3.4, 3.5",
        )

    def get_controller_version(self) -> version.Version:  # type:ignore[return-value,unused-ignore]
        """Return the version of the current controller."""
        agent_conf_path = pathlib.Path(hookenv.charm_dir()).joinpath("../agent.conf")
        with open(agent_conf_path, "r", encoding="utf-8") as conf_file:
            agent_conf = yaml.safe_load(conf_file)

        controller_version = agent_conf.get("upgradedToVersion")
        if not controller_version:
            raise RuntimeError("Charm failed to fetch controller's version.")

        return version.parse(controller_version)

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

    def generate_exporter_config(
        self,
    ) -> Dict[str, Union[Dict[str, Union[List[str], str, None]], str, None]]:
        """Generate exporter service config based on the values from charm config."""
        config = ExporterConfig(
            debug=self.config.get("debug"),
            customer=self.config.get("customer"),
            cloud=self.config.get("cloud-name"),
            controller=self.config.get("controller-url"),
            ca_cert=self.get_controller_ca_cert(),
            user=self.config.get("juju-user"),
            password=self.config.get("juju-password"),
            interval=self.config.get("scrape-interval"),
            port=self.config.get("scrape-port"),
            prefixes=self.config.get("virtual-macs"),
            match_interfaces=self.config.get("match-interfaces"),
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

    def _on_install(self, _: Optional[InstallEvent]) -> None:
        """Install prometheus-juju-exporter snap."""
        self.unit.status = MaintenanceStatus("Installing charm software.")
        try:
            self.exporter.install(self.snap_path, self.snap_channel)
        except snap.CouldNotAcquireLockException as exc:
            install_source = "local resource" if self.snap_path else "snap store"
            logger.error("Failed to install %s from %s.", self.exporter.SNAP_NAME, install_source)
            raise exc

    @evaluate_status
    def _on_upgrade_charm(self, _: UpgradeCharmEvent) -> None:
        """Process charm upgrade event.

        Since this event is triggered also when new resource is attached to the charm,
        we must re-install the snap and re-apply configuration
        """
        self._on_install(None)
        self._on_config_changed(None)

    def _on_stop(self, _: StopEvent) -> None:
        """Clean up exporter snap on charm's removal."""
        self.exporter.uninstall()

    @evaluate_status
    def _on_config_changed(self, _: Optional[ConfigChangedEvent]) -> None:
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

    def _on_prometheus_available(self, _: PrometheusConnected) -> None:
        """Trigger configuration of a prometheus scrape target."""
        self.reconfigure_scrape_target()

    @evaluate_status
    def _on_update_status(self, _: UpdateStatusEvent) -> None:
        """Assess unit's status."""


if __name__ == "__main__":  # pragma: nocover
    main(PrometheusJujuExporterCharm)
