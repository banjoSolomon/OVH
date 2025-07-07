# app.py
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import docker
from docker.errors import NotFound, APIError
from datetime import datetime
import humanize  # pip install humanize

# import time # Not directly used for time formatting, datetime is used

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Required for flash messages

# Initialize Docker client globally
# This assumes the Flask app has access to the Docker daemon socket.
try:
    client = docker.from_env()
    # Test connection to ensure Docker daemon is reachable
    client.ping()
    DOCKER_CLIENT_READY = True
    print("Successfully connected to Docker daemon.")
except Exception as e:
    print(f"Error connecting to Docker daemon: {e}")
    DOCKER_CLIENT_READY = False
    client = None  # Ensure client is None if connection fails


def format_size(size_bytes):
    """Formats bytes into human-readable string (e.g., 10MB, 2.5GB)."""
    if size_bytes is None:
        return "N/A"
    return humanize.naturalsize(size_bytes)


def get_container_details(container):
    """Extracts comprehensive details for a given container object."""
    details = {
        'id': container.id,
        'short_id': container.short_id,
        'name': container.name,
        'image_tag': container.image.tags[0] if container.image.tags else 'untagged',
        'status': container.status,
        'command': container.attrs['Config']['Cmd'] if container.attrs['Config']['Cmd'] else 'N/A',
        'ports': 'N/A',
        'created_at': 'N/A',
        'size': 'N/A'  # Size info might not be readily available without specific API calls or list(size=True)
    }

    # Extract ports
    if container.ports:
        port_mappings = []
        for private_port, public_bindings in container.ports.items():
            if public_bindings:
                for binding in public_bindings:
                    host_ip = binding.get('HostIp', '0.0.0.0')
                    host_port = binding.get('HostPort', '')
                    port_mappings.append(f"{host_ip}:{host_port}->{private_port}")
            else:
                port_mappings.append(f"{private_port}")
        details['ports'] = ', '.join(port_mappings)
    else:
        details['ports'] = 'No ports'

    # Extract created time
    if container.attrs and 'Created' in container.attrs:
        try:
            created_iso = container.attrs['Created']
            # Remove nanoseconds and 'Z' for simpler parsing
            created_dt = datetime.fromisoformat(created_iso.replace('Z', '+00:00'))
            details['created_at'] = created_dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            details['created_at'] = 'N/A'

    # Size: Note that 'SizeRw' and 'SizeRootFs' are often only populated when
    # containers.list(size=True) or container.inspect() is called.
    # For a general list view, it's common for this to be 'N/A' unless explicit API calls are made.
    if 'SizeRw' in container.attrs and 'SizeRootFs' in container.attrs:
        total_size = container.attrs['SizeRw'] + container.attrs['SizeRootFs']
        details['size'] = format_size(total_size)
    elif 'SizeRw' in container.attrs:  # Fallback for just SizeRw
        details['size'] = format_size(container.attrs['SizeRw'])
    else:
        details['size'] = 'N/A'

    return details


def get_container_stats():
    """Gets counts of running, exited, and total containers."""
    if not DOCKER_CLIENT_READY:
        return 0, 0, 0  # Return zeros if Docker client is not ready

    try:
        # Fetch all containers once
        all_containers = client.containers.list(all=True)

        # Filter based on exact status strings from the single all_containers list
        running_count = len([c for c in all_containers if c.status == 'running'])
        exited_count = len([c for c in all_containers if c.status == 'exited'])
        total_count = len(all_containers)  # Total count is simply the length of all_containers

        print(f"Dashboard Stats: Running={running_count}, Exited={exited_count}, Total={total_count}")
        return running_count, exited_count, total_count
    except APIError as e:
        print(f"Docker API error in get_container_stats: {e}")
        return 0, 0, 0
    except Exception as e:
        print(f"An unexpected error occurred in get_container_stats: {e}")
        return 0, 0, 0


def flash_message_and_redirect(message, category):
    """Helper function to flash a message and redirect to the index."""
    flash(message, category)
    return redirect(url_for('index'))


@app.route("/", methods=["GET"])
def index():
    if not DOCKER_CLIENT_READY:
        flash("Could not connect to Docker daemon. Please ensure Docker is running and accessible.", "error")
        return render_template(
            "index.html",
            containers=[],
            filter_type="all",
            running_count=0,
            exited_count=0,
            total_count=0
        )

    filter_type = request.args.get("filter", "running")
    container_list = []
    # error_message = None # No longer needed, using flash messages

    try:
        if filter_type == "running":
            # Use exact status string for filtering directly via Docker SDK filters
            raw_containers = client.containers.list(filters={'status': 'running'})
        elif filter_type == "exited":
            # Use exact status string for filtering
            raw_containers = client.containers.list(filters={'status': 'exited'})
        else:  # "all"
            raw_containers = client.containers.list(all=True)

        for c in raw_containers:
            container_list.append(get_container_details(c))

    except APIError as e:
        flash(f"Docker API Error: {e}", "error")
        print(f"Docker API Error: {e}")
    except Exception as e:
        flash(f"An unexpected error occurred: {e}", "error")
        print(f"An unexpected error occurred: {e}")

    running_count, exited_count, total_count = get_container_stats()

    return render_template(
        "index.html",
        containers=container_list,
        filter_type=filter_type,
        running_count=running_count,
        exited_count=exited_count,
        total_count=total_count
        # error_message=error_message # No longer passed directly
    )


@app.route("/container/<container_id>/start", methods=["POST"])
def start_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready.", "error")
    try:
        container = client.containers.get(container_id)
        container.start()
        return flash_message_and_redirect(f"Container '{container.name}' started successfully.", "success")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error starting container: {e}", "error")
    except Exception as e:
        return flash_message_and_redirect(f"An unexpected error occurred: {e}", "error")


@app.route("/container/<container_id>/stop", methods=["POST"])
def stop_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready.", "error")
    try:
        container = client.containers.get(container_id)
        container.stop()
        return flash_message_and_redirect(f"Container '{container.name}' stopped successfully.", "success")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error stopping container: {e}", "error")
    except Exception as e:
        return flash_message_and_redirect(f"An unexpected error occurred: {e}", "error")


@app.route("/container/<container_id>/restart", methods=["POST"])
def restart_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready.", "error")
    try:
        container = client.containers.get(container_id)
        container.restart()
        return flash_message_and_redirect(f"Container '{container.name}' restarted successfully.", "success")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error restarting container: {e}", "error")
    except Exception as e:
        return flash_message_and_redirect(f"An unexpected error occurred: {e}", "error")


@app.route("/container/<container_id>/remove", methods=["POST"])
def remove_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready.", "error")
    try:
        container = client.containers.get(container_id)
        # Only allow removal if exited
        if container.status == 'exited':  # Use exact status string
            container.remove()
            return flash_message_and_redirect(f"Container '{container.name}' removed successfully.", "success")
        else:
            return flash_message_and_redirect(f"Container '{container.name}' is not exited. Please stop it first.",
                                              "warning")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error removing container: {e}", "error")
    except Exception as e:
        return flash_message_and_redirect(f"An unexpected error occurred: {e}", "error")


@app.route("/container/<container_id>/logs", methods=["GET"])
def get_container_logs(container_id):
    print(f"Received request for logs for container ID: {container_id}")
    if not DOCKER_CLIENT_READY:
        print("Docker client not ready, returning 500 for logs request.")
        return jsonify({"logs": "Docker client not ready."}), 500
    try:
        container = client.containers.get(container_id)
        print(f"Found container '{container.name}' ({container.id}). Fetching logs...")
        # Fetch logs, decode them, and limit to last 1000 lines for performance
        logs = container.logs(tail=1000).decode('utf-8')
        print(f"Successfully fetched logs for '{container.name}'.")
        return jsonify({"logs": logs})
    except NotFound:
        print(f"Container with ID '{container_id}' not found by Docker SDK. Returning 404.")
        return jsonify({"logs": f"Container with ID '{container_id}' not found."}), 404
    except APIError as e:
        print(f"Docker API error fetching logs for '{container_id}': {e}. Returning 500.")
        return jsonify({"logs": f"Error fetching logs: {e}"}), 500
    except Exception as e:
        print(f"An unexpected error occurred while fetching logs for '{container_id}': {e}. Returning 500.")
        return jsonify({"logs": f"An unexpected error occurred while fetching logs: {e}"}), 500


@app.route("/container/<container_id>/metrics", methods=["GET"])
def get_container_metrics(container_id):
    print(f"Received request for metrics for container ID: {container_id}")
    if not DOCKER_CLIENT_READY:
        print("Docker client not ready, returning 500 for metrics request.")
        return jsonify({"error": "Docker client not ready."}), 500
    try:
        container = client.containers.get(container_id)
        print(f"Found container '{container.name}' ({container.id}). Attempting to fetch stats...")

        # FIX: Directly assign stats_data as container.stats(stream=False) returns a dictionary
        stats_data = container.stats(stream=False)

        if not stats_data:  # Check if the dictionary is empty, which could happen if no stats are available
            print(
                f"No stats data available for container '{container.name}' ({container_id}). It might be too new or not generating stats yet.")
            return jsonify({"error": f"No metrics data available for container '{container.name}'."}), 500

        print(f"Successfully received raw stats data for '{container.name}'.")
        # print(f"Raw stats data: {stats_data}") # Uncomment for very verbose debugging

        network_metrics = {}
        try:
            if 'networks' in stats_data:
                for net_name, net_data in stats_data['networks'].items():
                    network_metrics[net_name] = {
                        'rx_bytes': format_size(net_data.get('rx_bytes')),
                        'tx_bytes': format_size(net_data.get('tx_bytes'))
                    }
            print(f"Network metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing network metrics for '{container_id}': {e}")
            network_metrics = {'error': f'Failed to process network data: {e}'}

        block_io_metrics = {
            'read_bytes': 'N/A',
            'write_bytes': 'N/A'
        }
        try:
            if 'blkio_stats' in stats_data and 'io_service_bytes_recursive' in stats_data['blkio_stats']:
                for entry in stats_data['blkio_stats']['io_service_bytes_recursive']:
                    if entry['op'] == 'Read':
                        block_io_metrics['read_bytes'] = format_size(entry['value'])
                    elif entry['op'] == 'Write':
                        block_io_metrics['write_bytes'] = format_size(entry['value'])
            print(f"Block I/O metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing block I/O metrics for '{container_id}': {e}")
            block_io_metrics = {'error': f'Failed to process block I/O data: {e}'}

        cpu_usage = 'N/A'
        try:
            # CPU usage calculation based on Docker's formula:
            # ((cpu_delta / system_cpu_delta) * number_of_cpus) * 100.0
            if 'cpu_stats' in stats_data and 'precpu_stats' in stats_data:
                cpu_delta = stats_data['cpu_stats']['cpu_usage']['total_usage'] - \
                            stats_data['precpu_stats']['cpu_usage']['total_usage']
                system_cpu_delta = stats_data['cpu_stats']['system_cpu_usage'] - \
                                   stats_data['precpu_stats']['system_cpu_usage']
                online_cpus = stats_data['cpu_stats'].get('online_cpus', len(
                    stats_data['cpu_stats']['cpu_usage']['percpu_usage']) if 'percpu_usage' in stats_data['cpu_stats'][
                    'cpu_usage'] else 1)  # Fallback to 1 or number of percpu_usage if online_cpus is missing

                if system_cpu_delta > 0 and cpu_delta > 0:
                    cpu_usage = f"{round((cpu_delta / system_cpu_delta) * online_cpus * 100.0, 2)}%"
                else:
                    cpu_usage = "0.00%"
            print(f"CPU metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing CPU metrics for '{container_id}': {e}")
            cpu_usage = f'Failed to process CPU data: {e}'

        memory_usage = 'N/A'
        try:
            if 'memory_stats' in stats_data and 'usage' in stats_data['memory_stats'] and 'limit' in stats_data[
                'memory_stats']:
                mem_usage = stats_data['memory_stats']['usage']
                mem_limit = stats_data['memory_stats']['limit']
                if mem_limit > 0:
                    memory_usage = f"{format_size(mem_usage)} / {format_size(mem_limit)} ({round((mem_usage / mem_limit) * 100, 2)}%)"
                else:
                    memory_usage = f"{format_size(mem_usage)} / Unlimited"
            print(f"Memory metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing memory metrics for '{container_id}': {e}")
            memory_usage = f'Failed to process Memory data: {e}'

        metrics_data = {
            'network_io': network_metrics,
            'block_io': block_io_metrics,
            'cpu_usage': cpu_usage,
            'memory_usage': memory_usage,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        print(f"Successfully fetched and processed metrics for '{container.name}'.")
        return jsonify(metrics_data)
    except NotFound:
        print(f"Container with ID '{container_id}' not found by Docker SDK. Returning 404 for metrics.")
        return jsonify({"error": f"Container with ID '{container_id}' not found."}), 404
    except APIError as e:
        print(f"Docker API error fetching metrics for '{container_id}': {e}. Returning 500.")
        return jsonify({"error": f"Docker API error: {e}"}), 500
    except Exception as e:
        print(f"An unexpected error occurred while fetching metrics for '{container_id}': {e}. Returning 500.")
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
