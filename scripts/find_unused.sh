#!/usr/bin/env bash
# find_unused.sh - Report files that are NOT imported or executed by the
# core application, test suite, CLI, Alembic migrations, or schema tooling.
set -euo pipefail

PYTHON="${1:-./venv/Scripts/python.exe}"
ROOT="${2:-.}"

if [ ! -f "$PYTHON" ]; then
  echo "Usage: $0 [python_executable] [root_dir]" >&2
  exit 2
fi

TEMP=$(mktemp -d)
trap 'rm -rf "$TEMP"' EXIT

REACH_SCRIPT="$TEMP/reach.py"

cat > "$REACH_SCRIPT" <<'PYEOF'
import ast, os, sys, tokenize, io

root = sys.argv[1]
skip_paths = set(sys.argv[2].split(os.pathsep)) if len(sys.argv) > 2 else set()

def read(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def is_python(path):
    return path.endswith(".py")

def file_uses_app_or_engine(path):
    # Skip if it's obviously tooling that can import app/engine freely
    lower = path.lower()
    if any(x in lower for x in ["/tests/", "\\tests\\", "test_", "conftest", "scripts/verify"]):
        return False
    return True

def extract_imports_and_uses(path):
    # returns set of module-like names referenced by this file
    names = set()
    text = read(path)
    try:
        tree = ast.parse(text, filename=path)
    except Exception:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, (ast.Call, ast.Attribute, ast.Name)):
            # crude: capture any attribute accesses
            pass
    # Also find string literals that look like subprocess targets
    import re
    for m in re.findall(r'["\']([\w./]+\.(?:py|json))["\']', text):
        names.add(os.path.splitext(m.split("/")[-1])[0])
    return names

def is_in_package(path, pkg):
    norm = os.path.normpath(path)
    pkg_norm = os.path.normpath(pkg)
    return norm == pkg_norm or norm.startswith(pkg_norm + os.sep)

def is_pyfile(path):
    return is_python(path)

visited = set()
queue = []

# Core entrypoints the system actually runs/uses
entrypoints = [
    os.path.join(root, "app", "main.py"),
    os.path.join(root, "engine", "__main__.py"),
    os.path.join(root, "alembic", "env.py"),
    os.path.join(root, "alembic", "script.py.mako"),
    os.path.join(root, "scripts", "generate_schema.py"),
    os.path.join(root, "tests", "conftest.py"),
]

# Also add all test files as use-nodes (they import app/engine)
test_files = []
for dirpath, _, filenames in os.walk(os.path.join(root, "tests")):
    for fn in filenames:
        if fn.endswith(".py"):
            test_files.append(os.path.join(dirpath, fn))

entrypoints.extend(test_files)

# Also Alembic versions
for dirpath, _, filenames in os.walk(os.path.join(root, "alembic", "versions")):
    for fn in filenames:
        if fn.endswith(".py"):
            entrypoints.append(os.path.join(dirpath, fn))

# seed scripts are used by tooling, but we still treat them as sources in this scan
for dirpath, _, filenames in os.walk(os.path.join(root, "scripts")):
    for fn in filenames:
        if fn.endswith(".py"):
            entrypoints.append(os.path.join(dirpath, fn))

used_files = set()
import_graph = {}

def normalize(p):
    return os.path.normpath(os.path.abspath(p))

for ep in entrypoints:
    epn = normalize(ep)
    if os.path.exists(epn):
        used_files.add(epn)
        queue.append(epn)

while queue:
    cur = queue.pop()
    if cur in visited:
        continue
    visited.add(cur)
    try:
        imports = extract_imports_and_uses(cur)
    except Exception:
        imports = set()
    import_graph[cur] = imports
    # Resolve imports to files in project
    for mod in imports:
        if mod in ("app",):
            target = os.path.join(root, "app", "__init__.py")
            if os.path.exists(target) and normalize(target) not in used_files:
                used_files.add(normalize(target)); queue.append(normalize(target))
        elif mod == "engine":
            target = os.path.join(root, "engine", "__init__.py")
            if os.path.exists(target) and normalize(target) not in used_files:
                used_files.add(normalize(target)); queue.append(normalize(target))
        elif mod in ("alembic", "sqlalchemy", "fastapi", "pydantic", "pytest", "httpx", "uvicorn", "click", "argparse", "json", "pathlib", "os", "sys"):
            continue
        else:
            # Try to find a file in engine/ or app/ with that module name
            for pkg in ("engine", "app"):
                cand = os.path.join(root, pkg, mod + ".py")
                if os.path.exists(cand) and normalize(cand) not in used_files:
                    used_files.add(normalize(cand)); queue.append(normalize(cand))

# Now walk entire project and report files not in used_files, excluding venv/git/cache/pdf
skip_prefixes = [
    os.path.join(root, "venv"),
    os.path.join(root, ".git"),
    os.path.join(root, ".pytest_cache"),
    os.path.join(root, ".ruff_cache"),
    os.path.join(root, ".mypy_cache"),
    os.path.join(root, "__pycache__"),
    os.path.join(root, ".freebuff"),
]
skip_suffixes = (".pdf", ".db", ".coverage", ".log", ".pyc", ". Toml?", ".ini", ".example", ".json", ".md", ".yml", ".yaml", ".ps1", ".txt", ".cfg")

print("# Files not referenced by the core system, test suite, CLI, migrations, or schema tooling")
print("# (Does NOT mean 'safe to delete' — see report for nuance.)")
print("")
for dirpath, dirnames, filenames in os.walk(root):
    normdir = os.path.normpath(dirpath)
    if any(normdir.startswith(sp) for sp in skip_prefixes):
        dirnames.clear()
        continue
    # Skip hidden dirs except known tooling
    dirnames[:] = [d for d in dirnames if not d.startswith(".") or d in ("venv",)]
    for fn in filenames:
        path = os.path.join(dirpath, fn)
        norm = os.path.normpath(path)
        if norm in used_files:
            continue
        if any(fn.endswith(s) for s in (".pyc", ".db", ".coverage", ".log")):
            continue
        # Skip if file is clearly non-code and not part of system runtime
        lower = fn.lower()
        if lower.endswith(".pdf"):
            continue
        rel = os.path.relpath(norm, root)
        # Determine if this file is 'referenced' by name anywhere in used code
        referenced_by_name = False
        for used, mods in import_graph.items():
            try:
                if rel in read(used):
                    referenced_by_name = True
                    break
            except Exception:
                pass
        if referenced_by_name:
            print(f"# referenced-by-content: {rel}")
            continue
        if is_pyfile(path):
            print(f"PYTHON_UNUSED: {rel}")
        else:
            print(f"FILE: {rel}")
PYEOF

"$PYTHON" "$REACH_SCRIPT" "$ROOT" "$(printf '%s' "${TEMP}" | sed 's/ /\\ /g')"
