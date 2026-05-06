import os, json, time, subprocess, base64, urllib.request, urllib.error
from datetime import datetime, timezone

# Load .env manually (no external deps)
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GH_USER = "lydazab"
REPO_NAME = "Lyudmula"
PENDING_PATH = "cmds/pending.json"
RESULT_PATH = "cmds/result.json"
POLL_INTERVAL = 5
CMD_TIMEOUT = 120
STDOUT_LIMIT = 3000

API_BASE = f"https://api.github.com/repos/{GH_USER}/{REPO_NAME}/contents"

def gh_get(path):
    url = f"{API_BASE}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    content = base64.b64decode(data["content"]).decode()
    return json.loads(content), data["sha"]

def gh_put(path, content_dict, sha, message):
    url = f"{API_BASE}/{path}"
    body = json.dumps({
        "message": message,
        "content": base64.b64encode(json.dumps(content_dict, ensure_ascii=False).encode()).decode(),
        "sha": sha,
        "branch": "main",
    }).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as r:
        r.read()

def run_cmd(cmd):
    proc = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=CMD_TIMEOUT
    )
    stdout = proc.stdout[-STDOUT_LIMIT:] if len(proc.stdout) > STDOUT_LIMIT else proc.stdout
    return stdout, proc.stderr[-1000:], proc.returncode

def main():
    last_id = None
    print(f"[cmd_runner] started, polling {GH_USER}/{REPO_NAME} every {POLL_INTERVAL}s")
    while True:
        try:
            pending, p_sha = gh_get(PENDING_PATH)
            cmd_id = pending.get("id")
            cmd = pending.get("cmd", "")
            if cmd_id and cmd_id != last_id:
                print(f"[cmd_runner] executing id={cmd_id}: {cmd}")
                stdout, stderr, rc = run_cmd(cmd)
                ts = datetime.now(timezone.utc).isoformat()
                result = {"id": cmd_id, "cmd": cmd, "stdout": stdout, "stderr": stderr, "returncode": rc, "ts": ts}
                _, r_sha = gh_get(RESULT_PATH)
                gh_put(RESULT_PATH, result, r_sha, f"result: {cmd_id}")
                last_id = cmd_id
                print(f"[cmd_runner] done rc={rc}")
        except Exception as e:
            print(f"[cmd_runner] error: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
