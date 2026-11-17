"""Opt-in group-readable projections; never use for credentials or transaction state."""

import os
from pathlib import Path

from .core import PilotError, Store


def reader_gid(root):
    root = Path(root)
    if not root.exists():
        return None
    st = root.lstat()
    if root.is_symlink() or not root.is_dir() or st.st_uid != os.getuid():
        raise PilotError("Publication directory must belong to the runtime user.")
    mode = st.st_mode & 0o7777
    if mode == 0o700:
        return None
    if mode != 0o2750 or st.st_gid not in {os.getgid(), *os.getgroups()}:
        raise PilotError("Publication directory must be private or setgid reader-only.")
    if any(x.startswith("system.posix_acl") for x in os.listxattr(root)):
        raise PilotError("Publication ACLs require separate review.")
    return st.st_gid


class PublicationStore(Store):
    """Only catalog/restore projections may opt into an audited read-only group."""

    def __init__(self, root):
        root = Path(root).expanduser().absolute()
        self.reader_gid = reader_gid(root)
        if self.reader_gid is None:
            super().__init__(root)
        else:
            if root.resolve() != root or any((p / ".git").exists() for p in (root, *root.parents)):
                raise PilotError("Publication directory must be outside Git with real paths.")
            self.root = root

    def prepare_public_fd(self, fd):
        if self.reader_gid is not None:
            os.fchown(fd, -1, self.reader_gid)
            os.fchmod(fd, 0o640)


def publication_bytes(path):
    path = Path(path)
    gid = reader_gid(path.parent)
    st = path.lstat()
    allowed = 0o640 if gid is not None else 0o600
    if (
        path.is_symlink()
        or not path.is_file()
        or st.st_uid != os.getuid()
        or st.st_mode & 0o7777 not in (0o600, allowed)
        or (st.st_mode & 0o040 and st.st_gid != gid)
        or any(x.startswith("system.posix_acl") for x in os.listxattr(path))
    ):
        raise PilotError("Publication bytes must be owner-only or explicitly reader-group-only.")
    return path.read_bytes()


class PublicationReader:
    """Read existing projections as their owner or an OS-authorized reader.

    Never creates a directory, opens a writer lock, or grants access to state.
    The kernel still enforces traversal and read permissions for the actual user.
    """

    def __init__(self, root):
        self.root = Path(os.path.abspath(Path(root).expanduser()))
        if self.root.resolve() != self.root:
            raise PilotError("Reader publication path must not contain symlinks.")
        st = self.root.lstat()
        if not self.root.is_dir() or st.st_mode & 0o7777 not in (0o700, 0o2750):
            raise PilotError("Reader publication must be private or setgid reader-only.")
        if any(x.startswith("system.posix_acl") for x in os.listxattr(self.root)):
            raise PilotError("Publication ACLs require separate review.")
        self.owner, self.group = st.st_uid, st.st_gid

    def path(self, name):
        return Store.path(self, name)

    def bytes(self, name):
        path = self.path(name)
        st = path.lstat()
        if (
            path.is_symlink()
            or not path.is_file()
            or st.st_uid != self.owner
            or st.st_mode & 0o7777 not in (0o600, 0o640)
            or (st.st_mode & 0o040 and st.st_gid != self.group)
            or any(x.startswith("system.posix_acl") for x in os.listxattr(path))
        ):
            raise PilotError("Invalid reader publication file permissions.")
        return path.read_bytes()
