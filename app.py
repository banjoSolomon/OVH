from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import docker
from docker.errors import NotFound, APIError
from datetime import datetime
import humanize  # pip install humanize

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Required for flash messages

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
    """Extracts comprehensive details for a given container object."""
    details = {
        'id': container.id,
        'short_id': container.short_id,
        'name': container.name,
        'image_tag': container.image.tags[0] if container.image.tags else 'untagged',
        'status': container.status,  # Directly use container.status for flexibility
        'command': ' '.join(container.attrs['Config']['Cmd']) if container.attrs['Config']['Cmd'] else 'N/A',
        # Join command list to string
        'ports': 'N/A',
        'created_at': 'N/A',
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

    # Extract created time
    if container.attrs and 'Created' in container.attrs:
        try:
            created_iso = container.attrs['Created']
            # Python's datetime.fromisoformat can handle 'Z' with tzinfo.
            # However, if we want a simple local datetime string, it's often easier to strip 'Z' and just parse.
            # Or, better, handle timezone-aware parsing. For simplicity here, we'll keep the previous approach
            # of stripping Z and letting fromisoformat handle the offset correctly if present.
            created_dt = datetime.fromisoformat(created_iso.replace('Z', '+00:00'))
            details['created_at'] = created_dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            details['created_at'] = 'N/A'

    # Size: Note that 'SizeRw' and 'SizeRootFs' are often only populated when
    # containers.list(size=True) or container.inspect() is called.
    # For a general list view, it's common for this to be 'N/A' unless explicit API calls are made.
    # To get size, we would ideally need `container.reload()` or fetch with `client.containers.list(all=True, size=True)`
    # which can impact performance for large numbers of containers.
    # For the scope of this UI, if these aren't consistently populated, 'N/A' is an acceptable fallback.
    if 'SizeRw' in container.attrs and 'SizeRootFs' in container.attrs:
        total_size = container.attrs['SizeRw'] + container.attrs['SizeRootFs']
        details['size'] = format_size(total_size)
    elif 'SizeRw' in container.attrs:
        details['size'] = format_size(container.attrs['SizeRw'])
    else:
        details['size'] = 'N/A'  # Default to N/A if size info isn't directly in `container.attrs`

    return details


def get_container_stats():
    """Gets counts of running, exited, and total containers."""
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
        print(f"Docker API error in get_container_stats: {e}")
        flash(f"Error fetching dashboard statistics: {e}", "error")  # Flash error
        return 0, 0, 0
    except Exception as e:
        print(f"An unexpected error occurred in get_container_stats: {e}")
        flash(f"An unexpected error occurred while fetching dashboard statistics: {e}", "error")  # Flash error
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

    filter_type = request.args.get("filter", "all")  # Default to 'all' for a broader initial view
    container_list = []

    try:
        # Docker SDK's filters parameter expects a dictionary of lists for statuses
        if filter_type == "running":
            raw_containers = client.containers.list(filters={'status': ['running']})
        elif filter_type == "exited":
            raw_containers = client.containers.list(filters={'status': ['exited']})
        elif filter_type == "all":
            raw_containers = client.containers.list(all=True)
        # Add other filters if you implement them (e.g., 'paused', 'restarting')
        # elif filter_type == "paused":
        #     raw_containers = client.containers.list(filters={'status': ['paused']})
        else:  # Fallback for invalid filter type
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

    running_count, exited_count, total_count = get_container_stats()

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
        # Only allow removal if exited, or if force is true (not implemented in UI for now)
        if container.status == 'exited':
            container.remove()
            return flash_message_and_redirect(f"Container '{container.name}' removed successfully.", "success")
        else:
            flash_message_and_redirect(
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

        # --- Extract basic container info here ---
        # Uptime will be the 'StartedAt' timestamp; calculate human-readable in JS
        container_info = {
            'id': container.short_id,  # Use short ID for display
            'name': container.name,
            'image': container.image.tags[0] if container.image.tags else 'untagged',
            'status': container.status,
            'uptime': container.attrs['State']['StartedAt'] # ISO format string
        }
        # ----------------------------------------

        stats_data = container.stats(stream=False)  # stream=False gets current stats immediately

        if not stats_data:
            print(
                f"No stats data available for container '{container.name}' ({container_id}). It might be too new or not generating stats yet.")
            # Still return container info even if no stats data
            return jsonify({
                "error": f"No metrics data available for container '{container.name}'.",
                **container_info  # Include basic info
            }), 500

        print(f"Successfully received raw stats data for '{container.name}'.")

        network_metrics = {}
        try:
            if 'networks' in stats_data:
                for net_name, net_data in stats_data['networks'].items():
                    network_metrics[net_name] = {
                        'rx_bytes': format_size(net_data.get('rx_bytes', 0)),  # Default to 0 for missing keys
                        'tx_bytes': format_size(net_data.get('tx_bytes', 0))
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
                read_bytes_total = 0
                write_bytes_total = 0
                for entry in stats_data['blkio_stats']['io_service_bytes_recursive']:
                    if entry['op'] == 'Read':
                        read_bytes_total += entry['value']
                    elif entry['op'] == 'Write':
                        write_bytes_total += entry['value']
                block_io_metrics['read_bytes'] = format_size(read_bytes_total)
                block_io_metrics['write_bytes'] = format_size(write_bytes_total)
            print(f"Block I/O metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing block I/O metrics for '{container_id}': {e}")
            block_io_metrics = {'error': f'Failed to process block I/O data: {e}'}

        cpu_percentage_value = 'N/A'
        try:
            # CPU usage calculation based on Docker's formula:
            # ((cpu_delta / system_cpu_delta) * number_of_cpus) * 100.0
            cpu_stats = stats_data.get('cpu_stats', {})
            precpu_stats = stats_data.get('precpu_stats', {})

            if cpu_stats and precpu_stats:
                cpu_delta = cpu_stats.get('cpu_usage', {}).get('total_usage', 0) - \
                            precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
                system_cpu_delta = cpu_stats.get('system_cpu_usage', 0) - \
                                   precpu_stats.get('system_cpu_usage', 0)

                # online_cpus might not be present, fallback to number of percpu_usage or 1
                online_cpus = cpu_stats.get('online_cpus',
                                            len(cpu_stats.get('cpu_usage', {}).get('percpu_usage', [])) or 1)

                if system_cpu_delta > 0 and cpu_delta > 0 and online_cpus > 0:
                    cpu_percent = (cpu_delta / system_cpu_delta) * online_cpus * 100.0
                    cpu_percentage_value = f"{round(cpu_percent, 2)}" # As a string for frontend
                else:
                    cpu_percentage_value = "0.00"
            print(f"CPU metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing CPU metrics for '{container_id}': {e}")
            cpu_percentage_value = f'Failed to process CPU data: {e}'

        memory_usage_str = 'N/A'
        memory_limit_str = 'N/A'
        try:
            memory_stats = stats_data.get('memory_stats', {})
            if 'usage' in memory_stats and 'limit' in memory_stats:
                mem_usage = memory_stats['usage']
                mem_limit = memory_stats['limit']
                if mem_limit > 0:
                    memory_usage_str = f"{format_size(mem_usage)} / {format_size(mem_limit)} ({round((mem_usage / mem_limit) * 100, 2)}%)"
                    memory_limit_str = format_size(mem_limit)
                else:
                    memory_usage_str = f"{format_size(mem_usage)} / Unlimited"
                    memory_limit_str = "Unlimited"
            print(f"Memory metrics processed for '{container.name}'.")
        except Exception as e:
            print(f"Error processing memory metrics for '{container_id}': {e}")
            memory_usage_str = f'Failed to process Memory data: {e}'
            memory_limit_str = 'N/A'

        metrics_data = {
            **container_info, # Includes ID, Name, Image, Status, Uptime
            'network_io': network_metrics,
            'disk_io': block_io_metrics, # Renamed key to match frontend expectation
            'cpu_percentage': cpu_percentage_value,
            'memory_usage': memory_usage_str,
            'memory_limit': memory_limit_str, # Explicitly include memory_limit
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