from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import docker
from docker.errors import NotFound, APIError
from datetime import datetime
import humanize

app = Flask(__name__)  # Corrected from __noise__ to __name__
app.secret_key = 'your_secret_key_here' # Replace with a strong, random secret key!

# Initialize Docker client globally
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
    """Extracts comprehensive details for a given container object for the table view."""
    details = {
        'id': container.id,
        'short_id': container.short_id,
        'name': container.name,
        'image_tag': container.image.tags[0] if container.image.tags else 'untagged',
        'status': container.status,  # Directly use container.status for flexibility
        'command': ' '.join(container.attrs['Config']['Cmd']) if container.attrs['Config']['Cmd'] else 'N/A',
        # Join command list to string
        'ports': 'N/A',
        'created_at': 'N/A', # This will now store the ISO string or 'N/A'
        'size': 'N/A'
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

    # Extract created time (ISO format for client-side processing)
    if container.attrs and 'Created' in container.attrs:
        details['created_at'] = container.attrs['Created']
    else:
        details['created_at'] = 'N/A' # Or an empty string if you prefer

    # Size: Note that 'SizeRw' and 'SizeRootFs' are often only populated when
    # containers.list(size=True) or container.inspect() is called.
    if 'SizeRw' in container.attrs and 'SizeRootFs' in container.attrs:
        total_size = container.attrs['SizeRw'] + container.attrs['SizeRootFs']
        details['size'] = format_size(total_size)
    elif 'SizeRw' in container.attrs:
        details['size'] = format_size(container.attrs['SizeRw'])
    else:
        details['size'] = 'N/A'

    return details


def get_container_stats_summary():
    """Gets counts of running, exited, and total containers for dashboard cards."""
    if not DOCKER_CLIENT_READY:
        return 0, 0, 0

    try:
        all_containers = client.containers.list(all=True)

        running_count = len([c for c in all_containers if c.status == 'running'])
        exited_count = len([c for c in all_containers if c.status == 'exited'])
        total_count = len(all_containers)

        print(f"Dashboard Stats: Running={running_count}, Exited={exited_count}, Total={total_count}")
        return running_count, exited_count, total_count
    except APIError as e:
        print(f"Docker API error in get_container_stats_summary: {e}")
        flash(f"Error fetching dashboard statistics: {e}", "error")
        return 0, 0, 0
    except Exception as e:
        print(f"An unexpected error occurred in get_container_stats_summary: {e}")
        flash(f"An unexpected error occurred while fetching dashboard statistics: {e}", "error")
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

    filter_type = request.args.get("filter", "all")
    container_list = []

    try:
        if filter_type == "running":
            raw_containers = client.containers.list(filters={'status': ['running']})
        elif filter_type == "exited":
            raw_containers = client.containers.list(filters={'status': ['exited']})
        elif filter_type == "all":
            raw_containers = client.containers.list(all=True)
        else:
            flash(f"Invalid filter type '{filter_type}'. Displaying all containers.", "warning")
            raw_containers = client.containers.list(all=True)
            filter_type = "all"

        for c in raw_containers:
            container_list.append(get_container_details(c))

    except APIError as e:
        flash(f"Docker API Error fetching containers: {e}", "error")
        print(f"Docker API Error: {e}")
    except Exception as e:
        flash(f"An unexpected error occurred while listing containers: {e}", "error")
        print(f"An unexpected error occurred: {e}")

    running_count, exited_count, total_count = get_container_stats_summary()

    return render_template(
        "index.html",
        containers=container_list,
        filter_type=filter_type,
        running_count=running_count,
        exited_count=exited_count,
        total_count=total_count
    )


@app.route("/container/<container_id>/start", methods=["POST"])
def start_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready. Cannot start container.", "error")
    try:
        container = client.containers.get(container_id)
        container.start()
        return flash_message_and_redirect(f"Container '{container.name}' started successfully.", "success")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error starting container '{container_id}': {e}", "error")
    except Exception as e:
        return flash_message_and_redirect(f"An unexpected error occurred: {e}", "error")


@app.route("/container/<container_id>/stop", methods=["POST"])
def stop_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready. Cannot stop container.", "error")
    try:
        container = client.containers.get(container_id)
        container.stop(timeout=5)  # Add a timeout for graceful shutdown
        return flash_message_and_redirect(f"Container '{container.name}' stopped successfully.", "success")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error stopping container '{container_id}': {e}", "error")
    except Exception as e:
        return flash_message_and_redirect(f"An unexpected error occurred: {e}", "error")


@app.route("/container/<container_id>/restart", methods=["POST"])
def restart_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready. Cannot restart container.", "error")
    try:
        container = client.containers.get(container_id)
        container.restart(timeout=10)  # Add a timeout for restart
        return flash_message_and_redirect(f"Container '{container.name}' restarted successfully.", "success")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error restarting container '{container_id}': {e}", "error")
    except Exception as e:
        return flash_message_and_redirect(f"An unexpected error occurred: {e}", "error")


@app.route("/container/<container_id>/remove", methods=["POST"])
def remove_container(container_id):
    if not DOCKER_CLIENT_READY:
        return flash_message_and_redirect("Docker client not ready. Cannot remove container.", "error")
    try:
        container = client.containers.get(container_id)
        if container.status == 'exited':
            container.remove()
            return flash_message_and_redirect(f"Container '{container.name}' removed successfully.", "success")
        else:
            return flash_message_and_redirect(
                f"Container '{container.name}' is currently '{container.status}'. Please stop it first before removing.",
                "warning")
    except NotFound:
        return flash_message_and_redirect(f"Container with ID '{container_id}' not found.", "error")
    except APIError as e:
        return flash_message_and_redirect(f"Error removing container '{container_id}': {e}", "error")
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


# NEW ROUTE: For full container details (attrs) - static configuration and metadata
@app.route("/container/<string:container_id>/details", methods=["GET"])
def get_container_full_details(container_id):
    print(f"Received request for full details for container ID: {container_id}")
    if not DOCKER_CLIENT_READY:
        return jsonify({"error": "Docker client not ready."}), 500
    try:
        container = client.containers.get(container_id)
        details = container.attrs # This contains all the low-level details
        print(f"Successfully fetched full details for '{container.name}'.")
        return jsonify(details)
    except NotFound:
        print(f"Container with ID '{container_id}' not found. Returning 404.")
        return jsonify({"error": "Container not found"}), 404
    except APIError as e:
        print(f"Docker API error fetching full details for '{container_id}': {e}. Returning 500.")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        print(f"An unexpected error occurred while fetching full details for '{container_id}': {e}. Returning 500.")
        return jsonify({"error": str(e)}), 500


@app.route("/container/<container_id>/metrics", methods=["GET"])
def get_container_metrics(container_id):
    print(f"Received request for metrics for container ID: {container_id}")
    if not DOCKER_CLIENT_READY:
        print("Docker client not ready, returning 500 for metrics request.")
        return jsonify({"error": "Docker client not ready."}), 500
    try:
        container = client.containers.get(container_id)
        print(f"Found container '{container.name}' ({container.id}). Attempting to fetch stats...")

        stats_data = container.stats(stream=False)  # stream=False gets current stats immediately

        if not stats_data:
            print(
                f"No stats data available for container '{container.name}' ({container_id}). It might be too new or not generating stats yet.")
            return jsonify({
                "error": f"No metrics data available for container '{container.name}'."
            }), 500

        print(f"Successfully received raw stats data for '{container.name}'.")

        # Initialize default values
        cpu_percentage_value = 0.0
        memory_usage_mb = 0.0
        memory_limit_mb = 0.0
        memory_percentage_value = 0.0
        net_io_rx_mb = 0.0
        net_io_tx_mb = 0.0
        block_io_read_mb = 0.0
        block_io_write_mb = 0.0

        try:
            # CPU usage calculation based on Docker's formula:
            cpu_stats = stats_data.get('cpu_stats', {})
            precpu_stats = stats_data.get('precpu_stats', {})

            if cpu_stats and precpu_stats:
                cpu_delta = cpu_stats.get('cpu_usage', {}).get('total_usage', 0) - \
                            precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
                system_cpu_delta = cpu_stats.get('system_cpu_usage', 0) - \
                                   precpu_stats.get('system_cpu_usage', 0)

                online_cpus = cpu_stats.get('online_cpus',
                                            len(cpu_stats.get('cpu_usage', {}).get('percpu_usage', [])) or 1)

                if system_cpu_delta > 0 and cpu_delta > 0 and online_cpus > 0:
                    cpu_percent = (cpu_delta / system_cpu_delta) * online_cpus * 100.0
                    cpu_percentage_value = round(cpu_percent, 2)
            print(f"CPU metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing CPU metrics for '{container_id}': {e}")

        try:
            memory_stats = stats_data.get('memory_stats', {})
            if 'usage' in memory_stats and 'limit' in memory_stats:
                mem_usage_bytes = memory_stats['usage']
                mem_limit_bytes = memory_stats['limit']

                memory_usage_mb = round(mem_usage_bytes / (1024 * 1024), 2)
                memory_limit_mb = round(mem_limit_bytes / (1024 * 1024), 2)

                if mem_limit_bytes > 0:
                    memory_percentage_value = round((mem_usage_bytes / mem_limit_bytes) * 100, 2)
            print(f"Memory metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing memory metrics for '{container_id}': {e}")

        try:
            if 'networks' in stats_data:
                rx_bytes_total = 0
                tx_bytes_total = 0
                for net_data in stats_data['networks'].values():
                    rx_bytes_total += net_data.get('rx_bytes', 0)
                    tx_bytes_total += net_data.get('tx_bytes', 0)
                net_io_rx_mb = round(rx_bytes_total / (1024 * 1024), 2)
                net_io_tx_mb = round(tx_bytes_total / (1024 * 1024), 2)
            print(f"Network metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing network metrics for '{container_id}': {e}")

        try:
            if 'blkio_stats' in stats_data and 'io_service_bytes_recursive' in stats_data['blkio_stats']:
                read_bytes_total = 0
                write_bytes_total = 0
                for entry in stats_data['blkio_stats']['io_service_bytes_recursive']:
                    if entry['op'] == 'Read':
                        read_bytes_total += entry['value']
                    elif entry['op'] == 'Write':
                        write_bytes_total += entry['value']
                block_io_read_mb = round(read_bytes_total / (1024 * 1024), 2)
                block_io_write_mb = round(write_bytes_total / (1024 * 1024), 2)
            print(f"Block I/O metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing block I/O metrics for '{container_id}': {e}")

        metrics_data = {
            'cpu_percent': cpu_percentage_value,
            'memory_usage': memory_usage_mb,
            'memory_limit': memory_limit_mb,
            'memory_percent': memory_percentage_value,
            'net_io_rx': net_io_rx_mb,
            'net_io_tx': net_io_tx_mb,
            'block_io_read': block_io_read_mb,
            'block_io_write': block_io_write_mb,
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
        return jsonify({"error": f"An unexpected error occurred while fetching metrics: {e}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)