"""Check tracked-file hygiene; intentionally not a complete security audit."""
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[1]
files = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
patterns = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:password|client_secret|api_key)\s*[:=]\s*[\"'][A-Za-z0-9+/=_-]{16,}[\"']"),
]
failures = []
for name in filter(None, files):
    path = pathlib.Path(name)
    if (path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".pyc"}
            or path.name == ".env" or path.name.startswith(".env.")
            or any(part in {".venv", "venv", "__pycache__"} for part in path.parts)):
        failures.append(name + ": prohibited generated/credential file")
        continue
    raw = (root / path).read_bytes()
    if len(raw) > 1000000 or b"\x00" in raw:
        failures.append(name + ": binary or oversized artifact needs explicit review")
        continue
    text = raw.decode("utf-8")
    if any(pattern.search(text) for pattern in patterns):
        failures.append(name + ": credential-shaped content (value withheld)")
if failures:
    print("\n".join(failures))
    sys.exit(1)
print("Tracked-file hygiene passed; fixtures and prose still require human review.")
