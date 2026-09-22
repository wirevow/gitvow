"""Paths that hold credentials, kept out of everything gitvow stores.

Redaction works on values and is best effort by construction. This list works on paths and is exact: a file
whose path says it is a credential store is never snapshotted, never kept as an attribution blob, and never
quoted, whatever its content. It is loaded *after* the repository's own settings and only ever adds
exclusions, so a policy can extend it and cannot switch it off; a repository that wants a checked-in
`.env.example` captured renames it, which is the honest fix.

Classification is by path, never by content: a secret in `deploy/prod-values.yaml` is not covered here and is
the redaction layer's problem. Public halves (`.crt`, `.cer`, `.pub`) are deliberately not listed, and source
under `internal/secrets/` stays visible because only configuration-shaped files under a `secrets/` or
`credentials/` directory are excluded.
"""

from __future__ import annotations

import fnmatch
import posixpath

# basenames, matched case-insensitively at any depth
BASENAMES: tuple[str, ...] = (
    ".env",
    ".env.*",
    ".envrc",
    ".npmrc",
    ".netrc",
    "_netrc",
    ".pgpass",
    ".htpasswd",
    ".pypirc",
    ".dockercfg",
    ".boto",
    ".git-credentials",
    "credentials",
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    "credentials.ini",
    "credentials.toml",
    "credentials.tfrc.json",
    "application_default_credentials.json",
    "secrets.json",
    "secrets.yml",
    "secrets.yaml",
    "secrets.ini",
    "secrets.toml",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
)
# suffixes for key material and encrypted stores
SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".pkcs12",
    ".jks",
    ".keystore",
    ".truststore",
    ".ppk",
    ".kdbx",
    ".asc",
    ".gpg",
)
# well-known relative locations, matched as the trailing path components
LOCATIONS: tuple[str, ...] = (".docker/config.json", ".kube/config", ".aws/credentials")
# configuration-shaped files under a directory named secrets/ or credentials/, at any depth
SECRET_DIRS: tuple[str, ...] = ("secrets", "credentials")
CONFIG_SUFFIXES: tuple[str, ...] = (
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".toml",
    ".cfg",
    ".conf",
    ".properties",
    ".txt",
    ".enc",
)


def is_credential_path(path: str) -> bool:
    """True when `path` (repository-relative or absolute, any separator) names a credential store."""
    p = path.replace("\\", "/").strip("/").lower()
    if not p:
        return False
    base = posixpath.basename(p)
    if any(fnmatch.fnmatchcase(base, pat) for pat in BASENAMES):
        return True
    if base.endswith(SUFFIXES):
        return True
    if any(p == loc or p.endswith("/" + loc) for loc in LOCATIONS):
        return True
    parts = p.split("/")
    return len(parts) > 1 and any(d in SECRET_DIRS for d in parts[:-1]) and base.endswith(CONFIG_SUFFIXES)


def pathspec_excludes() -> list[str]:
    """The same list as git pathspec globs, for snapshots. Basenames and suffixes at any depth."""
    out: list[str] = []
    for pat in BASENAMES:
        out += [pat, f"**/{pat}"]
    for suf in SUFFIXES:
        out += [f"*{suf}", f"**/*{suf}"]
    for loc in LOCATIONS:
        out += [loc, f"**/{loc}"]
    for d in SECRET_DIRS:
        for suf in CONFIG_SUFFIXES:
            out += [f"{d}/**/*{suf}", f"**/{d}/**/*{suf}", f"{d}/*{suf}", f"**/{d}/*{suf}"]
    return out
