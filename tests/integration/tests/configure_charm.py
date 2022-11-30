#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Pre-test configuration of a prometheus-juju-exporter testing model."""

import logging
from base64 import b64encode
from pathlib import Path

import yaml
from zaza import model

from .test_charm import wait_for_model_settle

logger = logging.getLogger(__name__)

JUJU_LOCAL_DATA = Path("/home/ubuntu/.local/share/juju/")
ACCOUNT_FILE = JUJU_LOCAL_DATA.joinpath("accounts.yaml")
CONTROLLER_FILE = JUJU_LOCAL_DATA.joinpath("controllers.yaml")


def setup_juju_credentials() -> None:
    """Configure prometheus-juju-exporter with required juju credentials.

    Credentials are pulled form config files of a nested controller deployed by
    'juju-local' charm during the tests.
    """
    controller_units = model.get_units("juju-local")
    if len(controller_units) < 1:
        err = "Failed to find 'juju-local' units."
        logger.error(err)
        raise RuntimeError(err)

    # Get juju controller credentials
    juju_controller = controller_units[0].entity_id
    account_data = model.run_on_unit(juju_controller, f"cat {ACCOUNT_FILE}")
    try:
        accounts = yaml.safe_load(account_data["Stdout"])
        username = accounts["controllers"]["lxd"]["user"]
        password = accounts["controllers"]["lxd"]["password"]
    except KeyError as exc:
        logger.error(
            "Failed to parse juju credentials for controller deployed by 'juju-local' charm"
        )
        raise exc

    # Get juju controller endpoint details
    controller_data = model.run_on_unit(juju_controller, f"cat {CONTROLLER_FILE}")
    try:
        controllers = yaml.safe_load(controller_data["Stdout"])
        endpoint = controllers["controllers"]["lxd"]["api-endpoints"][0]
        ca_cert = str(controllers["controllers"]["lxd"]["ca-cert"])
    except KeyError as exc:
        logger.error(
            "Failed to parse juju endpoint for controller deployed by 'juju-local' charm."
        )
        raise exc

    # configure juju exporter
    model.set_application_config(
        "prometheus-juju-exporter",
        {
            "organization": "Test org",
            "cloud-name": "Test Cloud",
            "controller-url": endpoint,
            "controller-ca": b64encode(ca_cert.encode()).decode(encoding="ascii"),
            "juju-user": username,
            "juju-password": password,
            "scrape-interval": "1",
        },
    )

    wait_for_model_settle()
