#!/usr/bin/env python3
"""Check public files and build an explicit allowlisted source archive.

Never reads ignored configuration, CLI profiles, message caches or logs.
"""

import argparse
from fnmatch import fnmatch
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = (
    ".gitignore", "config.example.json", "server.py", "listener.py",
    "configuration.py", "setup.py", "run-listener.bat", "package.py",
    "README.md", "README.zh-CN.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
    "docs/setup.md", "docs/verification.md", "tests/test_project.py",
    ".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/bug_report.md",
    ".github/ISSUE_TEMPLATE/feature_request.md", ".github/pull_request_template.md",
)


def ignored(path):
    # Mirror repository ignore rules relevant to files. Prune directories before
    # traversal so profile/credential files are never opened, even by the scanner.
    relative = path.relative_to(ROOT)
    dirs = {".git", ".lark-cli", ".local", ".tools", "cards", "results", "__pycache__",
            ".venv", "venv", "node_modules", ".pytest_cache", "htmlcov", ".vscode", ".idea"}
    if any(part in dirs for part in relative.parts):
        return True
    patterns = ("config.json", "config.local.*", ".env", ".env.*", "*.local.*", "*.local.json",
                "*credentials*", "*secrets*", "*.pem", "*.key", "*.log", "*.log.*", "*.pid",
                "*.pyc", "*.pyo", ".coverage", ".DS_Store", "Thumbs.db")
    return path.name != ".env.example" and any(fnmatch(path.name, item) for item in patterns)


def candidates(directory=ROOT):
    for path in sorted(directory.iterdir()):
        if ignored(path):
            continue
        if path.is_symlink():
            yield path
        elif path.is_dir():
            yield from candidates(path)
        elif path.is_file():
            yield path


def check():
    issues = []
    # Match real-shaped identifiers, not generic config field names.
    patterns = {
        "identifier": re.compile(r"\b(?:o[uc]|cli)_[A-Za-z0-9]{16,}\b"),
        "personal path": re.compile(r"(?i)(?:[A-Z]:[/\\]+Users[/\\]+[^/\\\s\"'<>]+|/(?:Users|home)/[^/\s\"'<>]+|[A-Z]:[/\\]+ccfolder[/\\])"),
    }
    for path in candidates():
        name = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            issues.append(name + ": symlinks are not allowed in public source")
            continue
        if name not in PUBLIC_FILES:
            issues.append(name + ": not in PUBLIC_FILES; inspect before publishing")
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            issues.append(name + ": cannot inspect as UTF-8 text")
            continue
        for number, line in enumerate(content.splitlines(), 1):
            for label, pattern in patterns.items():
                if pattern.search(line):
                    issues.append(f"{name}:{number}: possible {label} (value hidden)")
    for name in PUBLIC_FILES:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            issues.append(name + ": missing or not a regular public file")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="scan without making an archive")
    args = parser.parse_args()
    issues = check()
    if issues:
        for issue in issues:
            print(issue)
        return 1
    print(f"Public source check passed ({len(PUBLIC_FILES)} files); ignored local data not read.")
    if not args.check:
        directory = ROOT / ".local/dist"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "desktop-feishu-mcp.zip"
        # A generated archive may be rebuilt; source files are never changed.
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in PUBLIC_FILES:
                archive.write(ROOT / name, "desktop-feishu-mcp/" + name)
        print("Created .local/dist/desktop-feishu-mcp.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
