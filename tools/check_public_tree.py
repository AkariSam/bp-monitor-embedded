#!/usr/bin/env python3
"""公开仓库树检查：禁止路径、敏感文本和明显密钥。

用法（仓库根目录）：
    python tools/check_public_tree.py

CI 和 pre-push 都调用此脚本。它不是通用密钥扫描器，只负责本仓库的边界。
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PREFIXES = (
    ("tmp",),
    (".venv",),
    ("__pycache__",),
    (".pytest_cache",),
    ("app",),
    ("build",),
    ("docs", "weekly"),
    ("docs", "research"),
    ("docs", "raw materials"),
    ("docs", "reference"),
    ("docs", "hardware"),
    ("firmware", "demo", "out"),
    ("algo", "c", "build"),
    ("algo", "out"),
    ("algo", "data", "sim"),
    ("algo", "data", "real"),
)

FORBIDDEN_NAMES = (
    ".env",
    "llm_config.dart",
    "key.properties",
)

FORBIDDEN_SUFFIXES = (
    ".apk",
    ".aab",
    ".jks",
    ".keystore",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
)

TEXT_PATTERNS = (
    ("person name", re.compile(r"周明宇|余方芸|鲁欣远|张静怡|王静怡|Lu Xinyuan")),
    ("school/course", re.compile(r"上海大学|翔英")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-like key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github token", re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("google api key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("bearer token", re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}")),
)


def _is_forbidden_path(rel):
    parts = rel.parts
    if "BloodPressureMonitor" in parts:
        return "old project prefix"
    for prefix in FORBIDDEN_PREFIXES:
        if parts[: len(prefix)] == prefix:
            if prefix in (("algo", "out"), ("algo", "data", "sim"), ("algo", "data", "real")):
                if rel.name == ".gitkeep":
                    continue
            return "/".join(prefix)
    return None


def _is_forbidden_name(rel):
    name = rel.name
    if name in FORBIDDEN_NAMES:
        return name
    if "回复" in name and name.endswith(".md"):
        return "internal review file"
    if name.endswith(FORBIDDEN_SUFFIXES):
        return name
    return None


def _read_text(path):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:2048]:
        return None
    return data.decode("utf-8", errors="ignore")


def main():
    errors = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if ".git" in rel.parts:
            continue

        reason = _is_forbidden_path(rel)
        if reason:
            errors.append("%s: forbidden path (%s)" % (rel, reason))
            continue

        reason = _is_forbidden_name(rel)
        if reason:
            errors.append("%s: forbidden file (%s)" % (rel, reason))
            continue

        # 本脚本自身包含检查规则，不扫描自己。
        if rel == Path("tools/check_public_tree.py"):
            continue

        text = _read_text(path)
        if text is None:
            continue
        for label, pattern in TEXT_PATTERNS:
            if pattern.search(text):
                errors.append("%s: forbidden text (%s)" % (rel, label))

    if errors:
        for line in errors:
            print("ERROR: " + line)
        print("\n%d problem(s) found" % len(errors))
        return 1

    print("public tree OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
