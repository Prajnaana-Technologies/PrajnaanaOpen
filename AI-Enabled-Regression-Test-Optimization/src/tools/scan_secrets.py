# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Refuse to ship a tree containing a credential.

A key committed to git stays in the history after the file is deleted, so the
only real fix is rotation at the provider. This catches the next one before it
gets that far.

Exit code 0 means clean, 1 means something was found -- so it works as a CI
gate and as a pre-commit hook:

    python tools/scan_secrets.py
    python tools/scan_secrets.py --path .. --quiet

It reads working-tree files only. It does NOT read git history, so a clean
result here says nothing about what is already committed.

Where git can answer, it reads only the files git would carry: anything
gitignored is skipped, and a credential in an ignored file is not one this
tool can leak. Where it cannot -- git not installed, or the path is not a
repository -- scan_walk reads the tree directly instead, which is the
normal case for an unpacked release.

Walking is wider than the git listing, because .gitignore is not consulted
and a file git would have hidden is read. It is not a scan of everything:
both paths drop the same names and directories, so neither reports a key in
.env, in build/, dist/, venv/ or _internal/ (ALLOWED_NAMES and SKIP_DIRS
below), and neither reads a file whose suffix is in SKIP_SUFFIXES. "Clean"
therefore means the same thing in both cases about the files that get
shipped, and means nothing at all about .env -- which is deliberate, and the
reason follows.

A leftover .env is skipped, not reported, so a local key does not make the
pre-commit hook refuse every commit. A key lives only in the environment of
the process that was given it and is written nowhere, so the scanner should
not see one at all; .env stays on the list for the copy someone still has
lying about.
"""

import argparse
import os
import re
import subprocess
import sys

# Patterns worth stopping a release for. Each is specific enough that a hit is
# almost certainly real -- a scanner that cries wolf gets switched off.
PATTERNS = (
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    # Google's newer key format. The dashboard accepts both forms (see its
    # KEY_PREFIXES), so a pasted AQ. key is caught here as an AIza one is.
    ("Google API key", re.compile(r"AQ\.[0-9A-Za-z_\-]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)

# Files that legitimately contain the shape of a credential without being
# one. .env is gitignored, so the git listing drops it anyway; naming it
# here drops it on the walk fallback too, which is what a packaged copy
# with no .git gets. The application does not read a .env, but someone may
# still have one lying about and it should be skipped, not reported -- so
# neither path reports a key in one, whichever it took.
ALLOWED_NAMES = {
    ".env",
    "scan_secrets.py",
    "SECURITY.md",
}

SKIP_DIRS = {
    ".git", "__pycache__", "venv", ".venv", "node_modules",
    "_internal", "build", "dist", ".pytest_cache",
}

# Binary-ish extensions that never hold a credential in readable form and
# would otherwise make the scan slow.
SKIP_SUFFIXES = (
    ".bin", ".hex", ".elf", ".o", ".a", ".so", ".dll", ".pyd", ".exe",
    ".wav", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".docx",
    ".pptx", ".drawio", ".jsonl",
)

MAX_BYTES = 2_000_000


class Finding(object):
    def __init__(self, path, line_no, label, excerpt):
        self.path = path
        self.line_no = line_no
        self.label = label
        self.excerpt = excerpt

    def __str__(self):
        return "{}:{}: {} -- {}".format(
            self.path, self.line_no, self.label, self.excerpt)


def _redact(match):
    """Show enough to find it, never enough to use it."""
    text = match.group(0)

    return text[:10] + "..." + "[redacted, {} chars]".format(len(text))


def scan_file(path, display=None):
    display = display or path

    findings = []

    try:
        if os.path.getsize(path) > MAX_BYTES:
            return findings

        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line_no, line in enumerate(handle, start=1):
                for label, pattern in PATTERNS:
                    match = pattern.search(line)

                    if match:
                        findings.append(
                            Finding(display, line_no, label, _redact(match))
                        )
    except OSError:
        return findings

    return findings


def _wanted(name):
    """False for a file that never holds a credential worth stopping for."""
    if name in ALLOWED_NAMES:
        return False

    return not name.lower().endswith(SKIP_SUFFIXES)


def git_listed(root):
    """Paths git would carry: tracked, plus untracked and not ignored.

    None when `root` is not a repository or git is not installed, which is
    the normal state of an unpacked release -- the caller walks the tree
    instead.
    """
    try:
        result = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    return [line.strip().strip('"') for line in result.stdout.splitlines()
            if line.strip()]


def scan_tree(root):
    listed = git_listed(root)

    if listed is not None:
        return scan_listed(root, listed)

    return scan_walk(root)


def scan_listed(root, paths):
    """Scan exactly the files git named, in sorted order."""
    findings = []

    for relative in sorted(paths):
        parts = relative.replace("\\", "/").split("/")

        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue

        if not _wanted(parts[-1]):
            continue

        path = os.path.join(root, *parts)

        if os.path.isfile(path):
            findings.extend(scan_file(path, display=relative))

    return findings


def scan_walk(root):
    """Every file under `root`. Used when git cannot answer."""
    findings = []

    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for name in sorted(filenames):
            if not _wanted(name):
                continue

            path = os.path.join(directory, name)

            findings.extend(
                scan_file(path, display=os.path.relpath(path, root))
            )

    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fail if the working tree contains a credential")
    parser.add_argument("--path", default=".", help="directory to scan")
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing when clean")

    args = parser.parse_args(argv)

    root = os.path.abspath(args.path)

    # Asked here so the note below can say which set of files was read.
    listed = git_listed(root)

    if listed is None:
        findings = scan_walk(root)
    else:
        findings = scan_listed(root, listed)

    if not findings:
        if not args.quiet:
            print("No credentials found in", root)
            print("Note: this reads the working tree only, not git "
                  "history.")

            if listed is None:
                print("git could not list this tree, so the tree was walked "
                      "instead:")
                print("whatever .gitignore keeps out was read too, but "
                      ".env, build/, dist/,")
                print("venv/ and _internal/ were still skipped.")
            else:
                print("It skips whatever .gitignore keeps out. The "
                      "application writes")
                print("no key to any file, so none should appear here.")

        return 0

    print("{} possible credential(s) found:".format(len(findings)))
    print()

    for finding in findings:
        print("  ", finding)

    print()
    print("A key in a commit stays in the history after the file is deleted.")
    print("Rotate it at the provider; removing the file is not enough.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
