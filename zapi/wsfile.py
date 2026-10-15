"""The workspace file on disk: JSON, compressed with zlib, then encrypted with Windows DPAPI (this user, this computer).

Layout:  b"ZAPIWS" + format version (1 byte) + DPAPI blob of zlib(JSON).
A file that does not start with the marker is an older plain-JSON workspace; it is read as is and rewritten in this format
on the next save."""
import json
import zlib

from . import vault

MAGIC = b"ZAPIWS"
VERSION = 1


class WorkspaceFileError(Exception):
    """The file is a Zapi workspace but cannot be opened here (other Windows account or computer, damage, newer format)."""


def is_packed(raw):
    return raw.startswith(MAGIC)


def pack(payload):
    """payload (a dict) -> bytes ready to write. Needs Windows (vault.available())."""
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return MAGIC + bytes([VERSION]) + vault.protect_bytes(zlib.compress(data, 6))


def unpack_text(raw):
    """The inner JSON text of a packed file."""
    if len(raw) <= len(MAGIC) or not is_packed(raw):
        raise WorkspaceFileError("This is not a Zapi workspace file.")
    version = raw[len(MAGIC)]
    if version > VERSION:
        raise WorkspaceFileError(f"This workspace was saved by a newer version of Zapi (format {version}).")
    try:
        return zlib.decompress(vault.unprotect_bytes(raw[len(MAGIC) + 1:])).decode("utf-8")
    except vault.VaultError:
        raise WorkspaceFileError("Windows could not decrypt it: it was saved under a different Windows account or on a "
                                 "different computer.") from None
    except (zlib.error, UnicodeDecodeError):
        raise WorkspaceFileError("The file is damaged.") from None


def unpack(raw):
    try:
        return json.loads(unpack_text(raw))
    except ValueError:
        raise WorkspaceFileError("The file is damaged.") from None


def read_text(path):
    """Readable JSON text of a workspace file, packed or plain (for diagnostics and tests)."""
    with open(path, "rb") as f:
        raw = f.read()
    return unpack_text(raw) if is_packed(raw) else raw.decode("utf-8")
