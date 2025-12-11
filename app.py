from flask import Flask, request, jsonify, render_template
import subprocess
import uuid
import os
import datetime
import zipfile
import tempfile
import shutil
from threading import Semaphore

app = Flask(__name__)

# -----------------------------------
# SETTINGS
# -----------------------------------
MAX_PARALLEL_CONTAINERS = 5
sema = Semaphore(MAX_PARALLEL_CONTAINERS)

# Create logs folder
if not os.path.exists("logs"):
    os.makedirs("logs")

# Execution limits
MAX_CODE_LENGTH = 5000
DOCKER_IMAGE_PY = "python:3.11-slim"
DOCKER_IMAGE_NODE = "node:18-slim"
EXECUTION_TIMEOUT = 10          # seconds (important: enforce 10s)
DOCKER_SUBPROCESS_TIMEOUT = 12  # small extra for docker CLI calls
MEMORY_LIMIT = "128m"
PIDS_LIMIT = 64
MAX_OUTPUT_BYTES = 200_000      # cap output to avoid huge responses


# -----------------------------------
# LOGGING FUNCTION
# -----------------------------------
def write_log(status, code, return_code, output, errors):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"""
----------------------------
TIME: {timestamp}
STATUS: {status}
RETURN CODE: {return_code}

USER CODE:
{code}

OUTPUT:
{output}

ERROR:
{errors}
----------------------------
"""
    with open("logs/executions.log", "a", encoding="utf-8") as f:
        f.write(log_entry)


def _truncate(s: str) -> str:
    if s is None:
        return ""
    b = s.encode("utf-8", errors="replace")
    if len(b) > MAX_OUTPUT_BYTES:
        return b[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace") + "\n\n[output truncated]"
    return s


# -----------------------------------
# HOME UI ROUTE
# -----------------------------------
@app.get("/")
def home():
    return render_template("index.html")


# -----------------------------------
# RUN SINGLE FILE (Python + JS) - improved safety
# -----------------------------------
@app.post("/run")
def run_code():
    acquired = sema.acquire(timeout=30)  # avoid deadlock: try acquire with timeout
    if not acquired:
        return jsonify({"error": "Server busy. Try again later."}), 429

    try:
        body = request.get_json() or {}
        code = body.get("code", "")
        language = body.get("language", "python")

        if not isinstance(code, str):
            return jsonify({"error": "Field 'code' must be a string."}), 400

        if len(code) > MAX_CODE_LENGTH:
            return jsonify({"error": "Code too long. Max 5000 chars allowed."}), 400

        run_id = uuid.uuid4().hex[:12]
        container_name = f"safe_exec_{run_id}"

        # choose image + run command; we will pass code via stdin (python - and node -)
        if language == "javascript" or language == "js":
            docker_image = DOCKER_IMAGE_NODE
            container_run_cmd = ["node", "-"]    # read script from stdin
        else:
            docker_image = DOCKER_IMAGE_PY
            container_run_cmd = ["python", "-"]  # read script from stdin

        # docker flags:
        docker_command = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network", "none",
            "--memory", MEMORY_LIMIT,
            "--pids-limit", str(PIDS_LIMIT),
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,size=16m",   # small writable /tmp inside container if needed
            "-i",                             # allow stdin (we pass the script)
            docker_image,
            *container_run_cmd
        ]

        # Run docker and pass code via stdin to avoid mounting the host FS
        try:
            proc = subprocess.run(
                docker_command,
                input=code.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=EXECUTION_TIMEOUT  # critical: kill after 10s
            )
        except subprocess.TimeoutExpired:
            # best-effort: try to force-remove the container (if still running)
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=DOCKER_SUBPROCESS_TIMEOUT
                )
            except Exception:
                pass
            write_log("TIMEOUT", code, -1, "", "Execution timed out")
            return jsonify({"output": "", "error": f"Execution timed out after {EXECUTION_TIMEOUT} seconds."}), 200
        except FileNotFoundError as e:
            write_log("SYSTEM ERROR", code, -1, "", str(e))
            return jsonify({"error": "Docker not found on the server. Ensure docker is installed and on PATH."}), 500
        except Exception as e:
            write_log("SYSTEM ERROR", code, -1, "", str(e))
            return jsonify({"error": str(e)}), 500

        stdout = _truncate(proc.stdout.decode("utf-8", errors="replace"))
        stderr = _truncate(proc.stderr.decode("utf-8", errors="replace"))
        return_code = proc.returncode

        # If process was killed by OOM or Docker, return helpful message
        if return_code != 0 and stdout == "" and stderr == "":
            status = "EXECUTION STOPPED (Killed by Docker/OS)"
            write_log(status, code, return_code, stdout, stderr)
            return jsonify({"output": stdout, "error": "Execution stopped: CPU or memory exceeded or killed."}), 500

        if return_code != 0:
            status = "RUNTIME ERROR"
            write_log(status, code, return_code, stdout, stderr)
            return jsonify({"output": stdout, "error": stderr}), 400

        status = "SUCCESS"
        write_log(status, code, return_code, stdout, stderr)
        return jsonify({"output": stdout, "error": ""})

    finally:
        sema.release()


# -----------------------------------
# EXECUTION HISTORY
# -----------------------------------
@app.get("/history")
def history():
    try:
        with open("logs/executions.log", "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "No history available."


# -----------------------------------
# RUN MULTIPLE FILES (.ZIP UPLOAD)
# -----------------------------------
@app.post("/upload_zip")
def upload_zip():
    acquired = sema.acquire(timeout=30)
    if not acquired:
        return jsonify({"error": "Server busy. Try again later."}), 429

    temp_dir = None
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        zip_file = request.files["file"]

        temp_dir = tempfile.mkdtemp(prefix="upload_")
        zip_path = os.path.join(temp_dir, "project.zip")
        zip_file.save(zip_path)

        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(temp_dir)
        except zipfile.BadZipFile:
            return jsonify({"error": "Invalid ZIP file"}), 400

        main_py = os.path.join(temp_dir, "main.py")
        main_js = os.path.join(temp_dir, "index.js")

        if os.path.exists(main_py):
            docker_image = DOCKER_IMAGE_PY
            run_cmd = ["python", "/app/main.py"]
        elif os.path.exists(main_js):
            docker_image = DOCKER_IMAGE_NODE
            run_cmd = ["node", "/app/index.js"]
        else:
            return jsonify({"error": "ZIP must contain main.py or index.js"}), 400

        # For zip uploads we MUST mount the temp_dir (isolated) so container can execute files.
        run_id = uuid.uuid4().hex[:12]
        container_name = f"safe_zip_{run_id}"

        docker_command = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network", "none",
            "--memory", MEMORY_LIMIT,
            "--pids-limit", str(PIDS_LIMIT),
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,size=16m",
            "-v", f"{temp_dir}:/app:ro",   # mount the extracted project read-only into /app
            docker_image,
            *run_cmd
        ]

        try:
            proc = subprocess.run(
                docker_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=EXECUTION_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            # cleanup container if needed
            try:
                subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=DOCKER_SUBPROCESS_TIMEOUT)
            except Exception:
                pass
            return jsonify({"output": "", "error": f"Execution timed out after {EXECUTION_TIMEOUT} seconds."}), 200
        except FileNotFoundError:
            return jsonify({"error": "Docker not found on the server. Ensure docker is installed and on PATH."}), 500

        stdout = _truncate(proc.stdout)
        stderr = _truncate(proc.stderr)

        return jsonify({
            "output": stdout,
            "error": stderr,
            "return_code": proc.returncode
        })

    finally:
        # cleanup
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        sema.release()


if __name__ == "__main__":
    # run on all interfaces for docker/local testing; enable debug during dev only
    app.run(host="0.0.0.0", port=5000, debug=True)
