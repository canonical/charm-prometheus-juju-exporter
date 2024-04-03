#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Functional tests for prometheus-juju-exporter."""

import logging
import os
import unittest
from typing import Dict

import juju.unit
import tenacity
import urllib3
from zaza import model

logger = logging.getLogger(__name__)


def wait_for_model_settle() -> None:
    """Wait for specific unit states that indicate settled model."""
    expected_app_states = {
        "ubuntu": {
            "workload-status-message-prefix": "",
        },
        "juju-local": {
            "workload-status-message-prefix": "",
        },
    }
    model.wait_for_application_states(states=expected_app_states)


class BasicPrometheusJujuExporterTests(unittest.TestCase):
    """Basic functional tests for prometheus-juju-exporter charm."""

    NAME = "prometheus-juju-exporter"
    CONTROLLER_APP = "juju-local"  # name of the application that deploys nested Juju controller.

    def setUp(self) -> None:
        """Configure resource before tests."""
        self.http = urllib3.PoolManager()
        self.unit = self.get_application_unit(self.NAME)
        self.controller = self.get_application_unit(self.CONTROLLER_APP)

    def exec_cmd(self, command: str, raise_on_fail: bool = True) -> Dict[str, str]:
        """Execute command on unit as the ubuntu user in the test environment.

        :param command: command including params
        :param raise_on_fail: If Exception should be raised if command fails.
        :return: Dict containing result of a command {'Code': '', 'Stderr': '', 'Stdout': ''}
        """
        controller_id = self.controller.entity_id
        cmd = f"sudo -u ubuntu {command}"

        result = model.run_on_unit(controller_id, cmd)

        return_code = int(result["Code"])
        if raise_on_fail and return_code != 0:
            stdout = result["Stdout"]
            stderr = result["Stderr"]
            msg = (
                f"Executing '{cmd}' on controller unit failed {os.linesep}"
                f"Return Code: {return_code}{os.linesep}"
                f"STDOUT: {stdout}{os.linesep}"
                f"STDERR: {stderr}"
            )
            logger.error(msg)
            raise RuntimeError("Execution of command on controller unit failed.")

        return result

    def get_config_value(self, config_option: str) -> str:
        """Return value of a specified config option."""
        return model.get_application_config(self.NAME)[config_option]["value"]

    def get_application_unit(self, app_name: str) -> juju.unit.Unit:
        """Return randomly selected unit af a specified application.

        This method will fail test if at least one unit of the application is not found.
        """
        all_units = model.get_units(app_name)
        if len(all_units) < 1:
            self.fail(f"Application {app_name} expected but not found.")

        return all_units[0]

    def set_scrape_port(self, port: int) -> None:
        """Set supplied port as a current scrape-port value.

        Value is reset back to the original value at the end of the test.
        """
        logger.info("Setting 'scrape-port' config value to %s for the rest of this test.", port)
        old_port = str(self.get_config_value("scrape-port"))

        model.set_application_config(self.NAME, {"scrape-port": str(port)})
        self.addCleanup(model.set_application_config, self.NAME, {"scrape-port": old_port})

        wait_for_model_settle()

    def add_machines(self, count: int, timeout: int = 600) -> None:
        """Add new model to the nested controller with specified number of machines.

        Machines do not run any workload and will be scraped at the end of the test.
        """
        model_name = "functest"
        logger.info("Adding model %s with %s units to the nested controller.", model_name, count)

        self.exec_cmd(f"juju add-model {model_name}")
        self.exec_cmd(f"juju add-machine -m {model_name} -n {count}")
        self.exec_cmd(f"juju-wait -m {model_name} --machine-pending-timeout {timeout}")

        self.addCleanup(
            self.exec_cmd,
            f"juju destroy-model --no-prompt {model_name} --force --destroy-storage --timeout 10m",
        )

    @tenacity.retry(
        wait=tenacity.wait_fixed(5),
        stop=tenacity.stop_after_attempt(12),
    )
    def validate_exporter(self, expected_machine_count: int = 1) -> None:
        """Verify that exporter exposes expected data on '/metrics' endpoint.

        This method is often called after config changes and therefore has grace period of 60
        seconds to reach expected result as the exporter service may still be restarting/settling.

        :param expected_machine_count: How many machines are expected to be in UP state
        """
        machine_count = 0
        unit_ip = self.unit.public_address
        scrape_port = self.get_config_value("scrape-port")
        endpoint = f"http://{unit_ip}:{scrape_port}/metrics"
        response = self.http.request("GET", endpoint)
        if response.status != 200:
            logger.error(
                "Request to %s resulted in unexpected status %s", endpoint, response.status
            )
            self.fail("Failed to reach exporter endpoint.")

        for line in response.data.decode("utf-8").splitlines():
            if line.startswith("juju_machine_state{") and line.endswith("1.0"):
                machine_count += 1

        self.assertEqual(
            machine_count,
            expected_machine_count,
            f"Expected number of machines: {expected_machine_count}, "
            f"exporter reported: {machine_count}",
        )
        logger.info("Exporter returns expected data when queried at %s", endpoint)

    def test_scrape_port_reconfiguration(self) -> None:
        """Test that exporter works after port reconfiguration."""
        self.validate_exporter(expected_machine_count=1)
        self.set_scrape_port(19000)
        self.validate_exporter(expected_machine_count=1)

    def test_data_refresh(self) -> None:
        """Test that after specified scrape interval, exporter shows updated data."""
        original_count = 1
        added_machines = 2
        expected_count = original_count + added_machines

        self.validate_exporter(expected_machine_count=original_count)
        self.add_machines(added_machines)
        self.validate_exporter(expected_machine_count=expected_count)
