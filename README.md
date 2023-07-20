<!--
Avoid using this README file for information that is maintained or published elsewhere, e.g.:

* metadata.yaml > published on Charmhub
* documentation > published on (or linked to from) Charmhub
* detailed contribution guide > documentation or CONTRIBUTING.md

Use links instead.
-->
[![Charmhub Badge](https://charmhub.io/prometheus-juju-exporter/badge.svg)](https://charmhub.io/prometheus-juju-exporter)
[![Release Edge](https://github.com/canonical/charm-prometheus-juju-exporter/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/charm-prometheus-juju-exporter/actions/workflows/release.yaml)

# prometheus-juju-exporter

This charm collects statistics about machines deployed by juju controller and exports them as a
Prometheus metrics.

Core collecting and exporting functionality is implemented in the
[prometheus-juju-exporter snap](https://github.com/canonical/prometheus-juju-exporter). This snap can be
either provided manually during the deployment as a resource or this charm will attempt to
download it from [Snap Store](https://snapcraft.io/store).

## Collected Data

Main focus of this exporter is to provide insight about how many machines are deployed by the Juju
controller and what's their state. The installed exporter snap will try to connect to the
controller and crawl through every model, collecting information about deployed machines.

Resulting metrics contain numeric representation `1` (UP) or `0` (DOWN) for each machine deployed
by the controller. In addition, each machine (metric) has labels that help to uniquely identify it:

* hostname - hostname of the machine as reported by Juju
* juju_model - name of the model in which the machine is deployed
* cloud_name - name of the cloud that hosts the model
* customer - name of the customer/organization that owns the controller
* type - (Experimental) Distinguishes various host types
  * metal - Physical machine
  * kvm - Virtual Machine
  * lxd - LXD container

Example data:
```
# HELP juju_machine_state Running status of juju machines
# TYPE juju_machine_state gauge
juju_machine_state{customer="DOC",hostname="juju-882749-controller-0",job="prometheus-juju-exporter",cloud_name="openstack-cloud-serverstack",juju_model="controller",type="kvm"} 1.0
juju_machine_state{customer="DOC",hostname="juju-882749-controller-3",job="prometheus-juju-exporter",cloud_name="openstack-cloud-serverstack",juju_model="controller",type="kvm"} 1.0
juju_machine_state{customer="DOC",hostname="juju-ad368d-test-0",job="prometheus-juju-exporter",cloud_name="openstack-cloud-serverstack",juju_model="test",type="kvm"} 1.0
juju_machine_state{customer="DOC",hostname="juju-ad368d-test-1",job="prometheus-juju-exporter",cloud_name="openstack-cloud-serverstack",juju_model="test",type="kvm"} 1.0
```

## Charm configuration

The charm requires certain configuration options to be set for it to function properly. Until all
required options are configured, the units will remain in `Blocked` state. User can examine unit
logs to see which required options are missing from configuration.

In particular, this charm needs juju credentials and endpoint of a Juju controller. This might
seem unnecessary as the charm is already being deployed in the juju environment but the fact is
that the charm unit itself has very little information about the cloud/model/controller that it's
deployed in. On its own, it's not capable of performing any introspection. That's why we need
the credentials, to connect to the controller as a regular juju client. You can create separate
Juju account for this purpose or in the development environments, you can reuse your own
user/password (usually found in `~/.local/share/juju/accounts.yaml`).

**Note:** Normally, **`superuser`** privilege is expected for such a user for full access to
the controller. However, in case it is not possible, the minimum privilege requirements are:
1. `login` access to the controller instance and `admin` access to the model hosting the controller
2. `admin` access to any model that's expected to be monitored by this exporter

Required options:

* `customer`
* `cloud-name`
* `controller-url`
* `juju-user`
* `juju-password`

(Use `juju config prometheus-juju-exporter` to get more information about each option.)

## Manual Deployment

This is currently (#TODO) the only way to deploy this charm as neither the charm nor the snap for
the exporter are published in their respective stores.

### Step 0 - Clone and build the snap
Clone [prometheus-juju-exporter snap](https://github.com/canonical/prometheus-juju-exporter) and build
the snap using `snapcraft`

```bash
git clone https://github.com/canonical/prometheus-juju-exporter.git
cd prometheus-juju-exporter/
make build
```

### Step 1 - Clone and build the charm
Clone this repository (if you haven't already) and run `make build`.

```bash
git clone https://github.com/canonical/charm-prometheus-juju-exporter.git
cd charm-prometheus-juju-exporter/
git submodule init
git submodule update
make build
```
### Step 2 - Deploy charm with snap as a resource
This is a subordinate charm, so we'll need additional principal charm for its deployment. We can
use the `ubuntu` charm for this. We'll also deploy `Prometheus` so we can work with collected data.

```bash
juju deploy ./prometheus-juju-exporter.charm --resource exporter-snap=<PATH_TO_EXPORTER_SNAP>
juju deploy ubuntu
juju deploy prometheus2
juju relate prometheus-juju-exporter ubuntu
juju relate prometheus2:scrape prometheus-juju-exporter
# (Optional) You can throw in Grafana into the mix
juju deploy grafana
juju relate grafana:grafana-source prometheus2
```

### Step 3 - Configuration
At this point the unit of `prometheus-juju-exporter` should be in `Blocked` state as it's missing
crucial configuration options. Following is a sample configuration:
```
juju config prometheus-juju-exporter \
  customer="Test Org" \
  cloud-name="Test Cloud" \
  controller-url="10.75.224.63:17070" \
  juju-user=admin \
  juju-password="86333204f5b22495550ab2c64a05607a"
```

### Step 4 - Verify deployment

To make sure that everything works, check status of the unit and try to fetch exported metrics.
```
$ juju status
Model    Controller  Cloud/Region         Version  SLA          Timestamp
billing  local-lxd   localhost/localhost  2.9.33   unsupported  16:44:57+01:00

App                       Version  Status  Scale  Charm                     Channel  Rev  Exposed  Message
grafana                            active      1  grafana                   stable    59  no       Ready
prometheus-juju-exporter           active      1  prometheus-juju-exporter             0  no       Unit is ready
prometheus2                        active      1  prometheus2               stable    33  no       Ready
ubuntu                    20.04    active      1  ubuntu                    stable    21  no

Unit                           Workload  Agent  Machine  Public address  Ports               Message
grafana/0*                     active    idle   2        10.75.224.197   3000/tcp            Ready
prometheus2/0*                 active    idle   1        10.75.224.118   9090/tcp,12321/tcp  Ready
ubuntu/0*                      active    idle   0        10.75.224.13
  prometheus-juju-exporter/0*  active    idle            10.75.224.13    5000/tcp            Unit is ready

```
```
$ curl http://10.75.224.13:5000/metrics
# HELP juju_machine_state Running status of juju machines
# TYPE juju_machine_state gauge
juju_machine_state{cloud_name="Test Cloud",customer="Test Org",hostname="juju-be56b1-0",job="prometheus-juju-exporter",juju_model="billing",type="metal"} 1.0
juju_machine_state{cloud_name="Test Cloud",customer="Test Org",hostname="juju-be56b1-1",job="prometheus-juju-exporter",juju_model="billing",type="metal"} 1.0
juju_machine_state{cloud_name="Test Cloud",customer="Test Org",hostname="juju-be56b1-2",job="prometheus-juju-exporter",juju_model="billing",type="metal"} 1.0
juju_machine_state{cloud_name="Test Cloud",customer="Test Org",hostname="juju-d53a52-0",job="prometheus-juju-exporter",juju_model="controller",type="metal"} 1.0
```
## Other resources

<!-- If your charm is documented somewhere else other than Charmhub, provide a link separately. -->

- [Read more](https://example.com)

- [Contributing](CONTRIBUTING.md) <!-- or link to other contribution documentation -->

- See the [Juju SDK documentation](https://juju.is/docs/sdk) for more information about developing and improving charms.
