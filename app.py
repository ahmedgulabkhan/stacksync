from flask import Flask, request, jsonify
import json
import os
import io
import sys
import tempfile
import subprocess

app = Flask(__name__)

NSJAIL_BIN = os.environ.get("NSJAIL_BIN", "/usr/bin/nsjail")
PYTHON_BIN = os.environ.get("PYTHON_BIN", "/usr/local/bin/python3")
EXECUTION_TIMEOUT_SECONDS = int(os.environ.get("EXECUTION_TIMEOUT_SECONDS", 30))
REQUEST_MAX_BYTES = int(os.environ.get("REQUEST_MAX_BYTES", 100_000_000))

def _validate_input(data):
    if not data or "script" not in data:
        return 'JSON must include key "script".'
    script = data["script"]
    if not isinstance(script, str) or not script.strip():
        return '"script" must be a non-empty string.'
    if len(script.encode("utf-8")) > REQUEST_MAX_BYTES:
        return f'"script" is too large (max {REQUEST_MAX_BYTES} bytes).'
    if "\0" in script:
        return "Script contains NUL bytes, which are not allowed."
    return None


# Executed inside the jail. Runs user_script.py, calls main(), and writes a 
# single JSON file with: 
# {"is_success": true, "result": ..., "stdout": "..."}
# or on error:
# {"is_success": false, "error": "...", "stdout": "..."}
def _runner_py() -> str:
    return r"""
import json, sys, io, os

OUT = 'runner_out.json'

def atomic_write(payload):
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, OUT)

buf = io.StringIO()
orig_stdout = sys.stdout
sys.stdout = buf

def finish_success(result):
    sys.stdout = orig_stdout
    atomic_write({"is_success": True, "result": result, "stdout": buf.getvalue()})
    raise SystemExit(0)

def finish_err(msg, code):
    sys.stdout = orig_stdout
    atomic_write({"is_success": False, "error": msg, "stdout": buf.getvalue()})
    raise SystemExit(code)

try:
    with open('user_script.py', 'r', encoding='utf-8') as f:
        code = f.read()

    ns = {"__name__": "__main__"}
    try:
        exec(compile(code, 'user_script.py', 'exec'), ns)
    except Exception as e:
        finish_err(f"Script execution error: {type(e).__name__}: {e}", 80)

    main = ns.get('main')
    if not callable(main):
        finish_err("No function main() found in script.", 81)

    try:
        result = main()
    except Exception as e:
        finish_err(f"main() raised: {type(e).__name__}: {e}", 82)

    try:
        json.dumps(result)
    except Exception:
        finish_err("main() must return a JSON value.", 83)

    finish_success(result)

except SystemExit:
    raise

except Exception as e:
    sys.stdout = orig_stdout
    atomic_write({"is_success": False, "error": f"Unexpected error: {type(e).__name__}: {e}", "stdout": buf.getvalue()})
    raise SystemExit(90)
"""

def _nsjail_cmd(sandbox_dir: str):
    ro_mounts = [
        "/usr", "/usr/local", "/bin", "/lib", "/lib64", "/etc/ssl", "/etc/alternatives"
    ]
    cmd = [
        NSJAIL_BIN,
        "--quiet",
        "--mode", "o",
        "--time_limit", str(EXECUTION_TIMEOUT_SECONDS),
        "--iface_no_lo",
        "--disable_proc",
        "--rlimit_as", "4096",
        "--rlimit_fsize", "10485760",
        "--rlimit_nofile", "128",
        "--user", "99999", "--group", "99999",
        "--hostname", "nsjail",
        "--cwd", "/sandbox",
        "--env", "LD_LIBRARY_PATH=/usr/local/lib",
    ]
    for p in ro_mounts:
        if os.path.exists(p):
            cmd.extend(["--bindmount_ro", p])

    cmd.extend(["--bindmount", f"{sandbox_dir}:/sandbox"])
    cmd.extend(["--", PYTHON_BIN, "runner.py"])
    return cmd


# Writes 'user_script.py' and 'runner.py' into a temp dir, 
# then runs the runner under nsjail and returns (is_success, result_or_error_str).
def _run_in_nsjail(script: str):
    if not os.path.exists(NSJAIL_BIN):
        return False, "nsjail binary not found. Set NSJAIL_BIN or install nsjail."

    with tempfile.TemporaryDirectory() as tmpdir:
        user_script = os.path.join(tmpdir, "user_script.py")
        runner = os.path.join(tmpdir, "runner.py")
        out_path = os.path.join(tmpdir, "runner_out.json")

        with open(user_script, "w", encoding="utf-8") as f:
            f.write(script)
        with open(runner, "w", encoding="utf-8") as f:
            f.write(_runner_py())

        cmd = _nsjail_cmd(tmpdir)
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=EXECUTION_TIMEOUT_SECONDS + 2,
            )
        except subprocess.TimeoutExpired:
            return False, f"Execution timed out after {EXECUTION_TIMEOUT_SECONDS} seconds."
        except Exception as e:
            return False, f"Failed to start sandbox: {type(e).__name__}: {e}"

        if not os.path.exists(out_path):
            err_tail = (completed.stderr or "").strip()[-500:]
            return False, "No output from sandboxed execution." + (f" [stderr: {err_tail}]" if err_tail else "")

        try:
            with open(out_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return False, "Malformed output from sandboxed execution."

        if payload.get("is_success") is True:
            return True, {"result": payload.get("result"), "stdout": payload.get("stdout", "")}

        return False, payload.get("error", "Unknown sandbox error")


@app.route("/execute", methods=["POST"])
def execute():
    if not request.is_json:
        return jsonify(error="Request must be application/json."), 400

    data = request.get_json(silent=True)
    err = _validate_input(data)
    if err:
        return jsonify(error=err), 400

    is_success, payload = _run_in_nsjail(data["script"])

    if is_success:
        return jsonify(payload), 200
    else:
        if "timed out" in str(payload).lower():
            return jsonify(error=payload), 408
        return jsonify(error=payload), 400


########################################################
################ WITHOUT NSJAIL #####################
########################################################
# from flask import Flask, request, jsonify
# import json
# import multiprocessing as mp
# import traceback
# import io
# import sys

# app = Flask(__name__)

# def _worker(script: str, q: mp.Queue) -> None:
#     old_stdout = sys.stdout
#     sys.stdout = io.StringIO()

#     try:
#         ns = {"__name__": "__main__"}
#         exec(script, ns)

#         main_func = ns.get("main")
#         if not callable(main_func):
#             q.put(("error", "No main() function found in script.", sys.stdout.getvalue()))
#             return

#         result = main_func()

#         try:
#             json.dumps(result)
#         except (TypeError, ValueError):
#             q.put(("error", "The main() function must return a JSON value", sys.stdout.getvalue()))
#             return

#         q.put(("is_success", result, sys.stdout.getvalue()))

#     except Exception as e:
#         err = "".join(traceback.format_exception_only(type(e), e)).strip()
#         q.put(("error", err, sys.stdout.getvalue()))
#     finally:
#         sys.stdout = old_stdout

# @app.route("/execute", methods=["POST"])
# def execute():
#     if not request.is_json:
#         return jsonify(error="Request must be application/json"), 400

#     data = request.get_json(silent=True)
#     if not data or "script" not in data:
#         return jsonify(error='JSON must include key "script"'), 400

#     script = data["script"]
#     if not isinstance(script, str) or not script.strip():
#         return jsonify(error='"script" must be a non-empty string'), 400

#     queue = mp.Queue()
#     process = mp.Process(target=_worker, args=(script, queue))
#     process.start()
#     process.join(30)

#     if process.is_alive():
#         process.terminate()
#         process.join()
#         return jsonify(error=f"Execution timed out after 30 seconds"), 408

#     if not queue.empty():
#         status, payload, captured_stdout = queue.get()
#         if status == "is_success":
#             return jsonify(result=payload, stdout=captured_stdout), 200
#         else:
#             return jsonify(error=payload), 400

#     return jsonify(error="Unknown execution error."), 500
