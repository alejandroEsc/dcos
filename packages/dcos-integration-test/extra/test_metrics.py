import logging
import uuid

import pytest

import retrying

__maintainer__ = 'mnaboka'
__contact__ = 'dcos-cluster-ops@mesosphere.io'


LATENCY = 60


def test_metrics_agents_ping(dcos_api_session):
    """ Test that the metrics service is up on masters.
    """
    for agent in dcos_api_session.slaves:
        response = dcos_api_session.metrics.get('/ping', node=agent)
        assert response.status_code == 200, 'Status code: {}, Content {}'.format(response.status_code, response.content)
        assert response.json()['ok'], 'Status code: {}, Content {}'.format(response.status_code, response.content)
        'agent.'

    for agent in dcos_api_session.public_slaves:
        response = dcos_api_session.metrics.get('/ping', node=agent)
        assert response.status_code == 200, 'Status code: {}, Content {}'.format(response.status_code, response.content)
        assert response.json()['ok'], 'Status code: {}, Content {}'.format(response.status_code, response.content)


@pytest.mark.supportedwindows
def test_metrics_masters_ping(dcos_api_session):
    for master in dcos_api_session.masters:
        response = dcos_api_session.metrics.get('/ping', node=master)
        assert response.status_code == 200, 'Status code: {}, Content {}'.format(response.status_code, response.content)
        assert response.json()['ok'], 'Status code: {}, Content {}'.format(response.status_code, response.content)


@pytest.mark.parametrize("prometheus_port", [61091, 61092])
def test_metrics_agents_prom(dcos_api_session, prometheus_port):
    for agent in dcos_api_session.slaves:
        response = dcos_api_session.session.request('GET', 'http://' + agent + ':{}/metrics'.format(prometheus_port))
        assert response.status_code == 200, 'Status code: {}'.format(response.status_code)


@pytest.mark.parametrize("prometheus_port", [61091, 61092])
def test_metrics_masters_prom(dcos_api_session, prometheus_port):
    for master in dcos_api_session.masters:
        response = dcos_api_session.session.request('GET', 'http://' + master + ':{}/metrics'.format(prometheus_port))
        assert response.status_code == 200, 'Status code: {}'.format(response.status_code)


def test_metrics_node(dcos_api_session):
    """Test that the '/system/v1/metrics/v0/node' endpoint returns the expected
    metrics and metric metadata.
    """
    def expected_datapoint_response(response):
        """Enure that the "node" endpoint returns a "datapoints" dict.
        """
        assert 'datapoints' in response, '"datapoints" dictionary not found'
        'in response, got {}'.format(response)

        for dp in response['datapoints']:
            assert 'name' in dp, '"name" parameter should not be empty, got {}'.format(dp)
            if 'filesystem' in dp['name']:
                assert 'tags' in dp, '"tags" key not found, got {}'.format(dp)

                assert 'path' in dp['tags'], ('"path" tag not found for filesystem metric, '
                                              'got {}'.format(dp))

                assert len(dp['tags']['path']) > 0, ('"path" tag should not be empty for '
                                                     'filesystem metrics, got {}'.format(dp))

        return True

    def expected_dimension_response(response):
        """Ensure that the "node" endpoint returns a dimensions dict that
        contains a non-empty string for cluster_id.
        """
        assert 'dimensions' in response, '"dimensions" object not found in'
        'response, got {}'.format(response)

        assert 'cluster_id' in response['dimensions'], '"cluster_id" key not'
        'found in dimensions, got {}'.format(response)

        assert response['dimensions']['cluster_id'] != "", 'expected cluster to contain a value'

        return True

    # Retry for 30 seconds for for the node metrics content to appear.
    @retrying.retry(stop_max_delay=30000)
    def wait_for_node_response(node):
        response = dcos_api_session.metrics.get('/node', node=node)
        assert response.status_code == 200
        return response

    # private agents
    for agent in dcos_api_session.slaves:
        response = wait_for_node_response(agent)

        assert response.status_code == 200, 'Status code: {}, Content {}'.format(
            response.status_code, response.content)
        assert expected_datapoint_response(response.json())
        assert expected_dimension_response(response.json())

    # public agents
    for agent in dcos_api_session.public_slaves:
        response = wait_for_node_response(agent)

        assert response.status_code == 200, 'Status code: {}, Content {}'.format(
            response.status_code, response.content)
        assert expected_datapoint_response(response.json())
        assert expected_dimension_response(response.json())

    # masters
    for master in dcos_api_session.masters:
        response = wait_for_node_response(master)

        assert response.status_code == 200, 'Status code: {}, Content {}'.format(
            response.status_code, response.content)
        assert expected_datapoint_response(response.json())
        assert expected_dimension_response(response.json())


def test_metrics_containers(dcos_api_session):
    """If there's a deployed container on the slave, iterate through them to check for
    the statsd-emitter executor. When found, query it's /app endpoint to test that
    it's sending the statsd metrics as expected.
    """
    # Helper func to check for non-unique CID's in a given /containers/id endpoint
    def check_cid(registry):
        if len(registry) <= 1:
            return True

        cid1 = registry[len(registry) - 1]
        cid2 = registry[len(registry) - 2]
        if cid1 != cid2:
            raise ValueError('{} != {}'.format(cid1, cid2))

        return True

    def check_tags(tags: dict, expected_tag_names: set):
        """Assert that tags contains only expected keys with nonempty values."""
        assert set(tags.keys()) == expected_tag_names
        for tag_name, tag_val in tags.items():
            assert tag_val != '', 'Value for tag "%s" must not be empty'.format(tag_name)

    @retrying.retry(wait_fixed=2000, stop_max_delay=LATENCY * 1000)
    def test_containers(app_endpoints):

        debug_task_name = []

        for agent in app_endpoints:

            # Retry for two and a half minutes since the collector collects
            # state every 2 minutes to propogate containers to the API
            @retrying.retry(wait_fixed=2000, stop_max_delay=150000)
            def wait_for_container_propogation():
                response = dcos_api_session.metrics.get('/containers', node=agent.host)
                assert response.status_code == 200
                assert len(response.json()) > 0, 'must have at least 1 container'

            wait_for_container_propogation()

            response = dcos_api_session.metrics.get('/containers', node=agent.host)
            for c in response.json():
                # Test that /containers/<id> responds with expected data
                container_id_path = '/containers/{}'.format(c)

                # Retry for 30 seconds for each container to present its content.
                @retrying.retry(stop_max_delay=30000)
                def wait_for_container_response():
                    response = dcos_api_session.metrics.get(container_id_path, node=agent.host)
                    assert response.status_code == 200
                    return response

                container_response = wait_for_container_response()
                assert 'datapoints' in container_response.json(), 'got {}'.format(container_response.json())

                cid_registry = []
                for dp in container_response.json()['datapoints']:
                    # Verify expected tags are present.
                    assert 'tags' in dp, 'got {}'.format(dp)
                    expected_tag_names = {
                        'container_id',
                    }
                    if 'executor_name' in dp['tags']:
                        # if present we want to make sure it has a valid value.
                        expected_tag_names.add('executor_name')
                    if dp['name'].startswith('blkio.'):
                        # blkio stats have 'blkio_device' tags.
                        expected_tag_names.add('blkio_device')
                    check_tags(dp['tags'], expected_tag_names)

                    # Ensure all container ID's in the container/<id> endpoint are
                    # the same.
                    cid_registry.append(dp['tags']['container_id'])
                    assert(check_cid(cid_registry))

                assert 'dimensions' in container_response.json(), 'got {}'.format(container_response.json())
                assert 'task_name' in container_response.json()['dimensions'], 'got {}'.format(
                    container_response.json()['dimensions'])

                debug_task_name.append(container_response.json()['dimensions']['task_name'])

                # looking for "statsd-emitter"
                if 'statsd-emitter' == container_response.json()['dimensions']['task_name']:
                    # Test that /app response is responding with expected data
                    app_response = dcos_api_session.metrics.get('/containers/{}/app'.format(c), node=agent.host)
                    assert app_response.status_code == 200
                    'got {}'.format(app_response.status_code)

                    # Ensure all /container/<id>/app data is correct
                    assert 'datapoints' in app_response.json(), 'got {}'.format(app_response.json())

                    # We expect three datapoints, could be in any order
                    uptime_dp = None
                    for dp in app_response.json()['datapoints']:
                        if dp['name'] == 'statsd_tester_time_uptime':
                            uptime_dp = dp
                            break

                    # If this metric is missing, statsd-emitter's metrics were not received
                    assert uptime_dp is not None, 'got {}'.format(app_response.json())

                    datapoint_keys = ['name', 'value', 'unit', 'timestamp', 'tags']
                    for k in datapoint_keys:
                        assert k in uptime_dp, 'got {}'.format(uptime_dp)

                    expected_tag_names = {
                        'dcos_cluster_id',
                        'test_tag_key',
                        'dcos_cluster_name',
                        'host'
                    }
                    check_tags(uptime_dp['tags'], expected_tag_names)
                    assert uptime_dp['tags']['test_tag_key'] == 'test_tag_value', 'got {}'.format(uptime_dp)

                    assert 'dimensions' in app_response.json(), 'got {}'.format(app_response.json())

                    return True

        assert False, 'Did not find statsd-emitter container, executor IDs found: {}'.format(debug_task_name)

    marathon_config = {
        "id": "/statsd-emitter",
        "cmd": "/opt/mesosphere/bin/./statsd-emitter -debug",
        "cpus": 0.5,
        "mem": 128.0,
        "instances": 1
    }
    with dcos_api_session.marathon.deploy_and_cleanup(marathon_config, check_health=False):
        endpoints = dcos_api_session.marathon.get_app_service_endpoints(marathon_config['id'])
        assert len(endpoints) == 1, 'The marathon app should have been deployed exactly once.'
        test_containers(endpoints)


def test_standalone_container_metrics(dcos_api_session):
    """
    An operator should be able to launch a standalone container using the
    LAUNCH_CONTAINER call of the agent operator API. Additionally, if the
    process running within the standalone container emits statsd metrics, they
    should be accessible via the DC/OS metrics API.
    """
    # Fetch the mesos master state to get an agent ID
    master_ip = dcos_api_session.masters[0]
    r = dcos_api_session.get('/state', host=master_ip, port=5050)
    assert r.status_code == 200
    state = r.json()

    # Find hostname and ID of an agent
    assert len(state['slaves']) > 0, 'No agents found in master state'
    agent_hostname = state['slaves'][0]['hostname']
    agent_id = state['slaves'][0]['id']
    logging.debug('Selected agent %s at %s', agent_id, agent_hostname)

    def _post_agent(json):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        r = dcos_api_session.post(
            '/api/v1',
            host=agent_hostname,
            port=5051,
            headers=headers,
            json=json,
            data=None,
            stream=False)
        return r

    # Prepare container ID data
    container_id = {'value': 'test-standalone-%s' % str(uuid.uuid4())}

    # Launch standalone container. The command for this container executes a
    # binary installed with DC/OS which will emit statsd metrics.
    launch_data = {
        'type': 'LAUNCH_CONTAINER',
        'launch_container': {
            'command': {'value': '/opt/mesosphere/bin/statsd-emitter'},
            'container_id': container_id,
            'resources': [
                {
                    'name': 'cpus',
                    'scalar': {'value': 0.2},
                    'type': 'SCALAR'
                },
                {
                    'name': 'mem',
                    'scalar': {'value': 64.0},
                    'type': 'SCALAR'
                },
                {
                    'name': 'disk',
                    'scalar': {'value': 1024.0},
                    'type': 'SCALAR'
                }
            ],
            'container': {
                'type': 'MESOS'
            }
        }
    }

    # There is a short delay between the container starting and metrics becoming
    # available via the metrics service. Because of this, we wait up to 10
    # seconds for these metrics to appear before throwing an exception.
    def _should_retry_metrics_fetch(response):
        return response.status_code == 204

    @retrying.retry(wait_fixed=1000,
                    stop_max_delay=10000,
                    retry_on_result=_should_retry_metrics_fetch,
                    retry_on_exception=lambda x: False)
    def _get_metrics():
        master_response = dcos_api_session.get(
            '/system/v1/agent/%s/metrics/v0/containers/%s/app' % (agent_id, container_id['value']),
            host=master_ip)
        return master_response

    r = _post_agent(launch_data)
    assert r.status_code == 200, 'Received unexpected status code when launching standalone container'

    try:
        logging.debug('Successfully created standalone container with container ID %s', container_id['value'])

        # Verify that the standalone container's metrics are being collected
        r = _get_metrics()
        assert r.status_code == 200, 'Received unexpected status code when fetching standalone container metrics'

        metrics_response = r.json()
        metric_keys = [datapoint['name'] for datapoint in metrics_response['datapoints']]
        assert 'statsd_tester_time_uptime' in metric_keys
        assert metrics_response['dimensions']['container_id'] == container_id['value']
    finally:
        # Clean up the standalone container
        kill_data = {
            'type': 'KILL_CONTAINER',
            'kill_container': {
                'container_id': container_id
            }
        }

        _post_agent(kill_data)
