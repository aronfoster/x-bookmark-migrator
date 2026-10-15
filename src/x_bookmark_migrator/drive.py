"""Create-only Drive storage using a human owner's offline OAuth grant.

Callers hold Store.locked for the entire operation. No Drive PATCH/DELETE exists.
Pre-generated IDs make interrupted creates recoverable without duplicate files.
"""

import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from .core import PilotError

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
MAX_BYTES = 256 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()


def drive_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise PilotError("Invalid Drive identifier.")
    return value


def checked(response, allowed=(200, 201)):
    if response.status_code not in allowed:
        raise PilotError(
            f"Drive HTTP {response.status_code}; stopped without automatic retry. "
            "Check grant, file visibility, quota and access before resuming.",
            code="storage_failed",
        )
    return response


def read_bounded(response, limit):
    checked(response, (200,))
    length = response.headers.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > limit):
        raise PilotError("Download exceeds the byte limit.", code="byte_limit")
    data = bytearray()
    for chunk in response.iter_bytes(65536):
        if len(data) + len(chunk) > limit:
            raise PilotError("Download exceeds the byte limit.", code="byte_limit")
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise PilotError("Download length mismatch.", code="length_mismatch")
    return bytes(data)


class Drive:
    def __init__(self, store, http, root, max_bytes=64 * 1024 * 1024):
        if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_BYTES:
            raise PilotError("Storage byte limit must be between 1024 and 268435456.")
        self.store, self.http, self.root = store, http, drive_id(root)
        self.max_bytes = max_bytes
        self.access = None
        self.expires = 0
        self.journal = store.read("drive-journal.private.json") or {}

    def headers(self):
        if time.time() >= self.expires:
            grant = self.store.read("drive-credentials.private.json")
            if not isinstance(grant, dict) or grant.get("type") != "authorized_user":
                raise PilotError("Configure owner OAuth: see docs/ARCHIVE.md, Drive authorization.")
            if not all(
                isinstance(grant.get(k), str) and grant[k]
                for k in ("client_id", "client_secret", "refresh_token")
            ):
                raise PilotError("Drive offline OAuth grant is incomplete.")
            response = checked(
                self.http.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "refresh_token",
                        **{k: grant[k] for k in ("client_id", "client_secret", "refresh_token")},
                    },
                )
            ).json()
            if (
                not isinstance(response.get("access_token"), str)
                or str(response.get("token_type", "")).lower() != "bearer"
                or type(response.get("expires_in")) is not int
                or response["expires_in"] <= 60
            ):
                raise PilotError("Invalid Drive token response.")
            self.access = response["access_token"]
            self.expires = time.time() + response["expires_in"] - 60
            self.scope = response.get("scope", "not_reported")
            if response.get("refresh_token"):
                grant["refresh_token"] = response["refresh_token"]
                self.store.write("drive-credentials.private.json", grant)
        return {"Authorization": "Bearer " + self.access, "Accept-Encoding": "identity"}

    def request(self, method, path, **kwargs):
        return self.http.request(method, API + path, headers=self.headers(), **kwargs)

    def metadata(self, file_id, missing_ok=False):
        result = self.request(
            "GET",
            "/files/" + drive_id(file_id),
            params={
                "fields": "id,name,mimeType,parents,size,trashed,ownedByMe,"
                "capabilities,appProperties"
            },
        )
        if result.status_code == 404 and missing_ok:
            return None
        value = checked(result).json()
        if value.get("id") != file_id or value.get("trashed") is not False:
            raise PilotError("Drive returned a missing, trashed or mismatched file.")
        return value

    def preflight(self):
        root = self.metadata(self.root)
        if (
            root.get("mimeType") != FOLDER
            or root.get("ownedByMe") is not True
            or root.get("capabilities", {}).get("canAddChildren") is not True
        ):
            raise PilotError("Archive root must be a writable folder owned by this OAuth user.")
        about = checked(
            self.request("GET", "/about", params={"fields": "user(permissionId),storageQuota"})
        ).json()
        owner = about.get("user", {}).get("permissionId")
        if not isinstance(owner, str) or not owner:
            raise PilotError("Drive did not return the owner's identity.")
        pin = {"root": self.root, "owner_permission_id": owner}
        prior = self.store.read("drive-destination.private.json")
        if prior and prior != pin:
            raise PilotError("Drive identity/destination changed; use a separate state directory.")
        self.store.write("drive-destination.private.json", pin)
        return {**pin, "reported_scope": self.scope, "storage_quota": about.get("storageQuota")}

    def download(self, file_id, limit=None):
        with self.http.stream(
            "GET",
            API + "/files/" + drive_id(file_id),
            params={"alt": "media"},
            headers=self.headers(),
        ) as response:
            return read_bounded(response, min(limit or self.max_bytes, self.max_bytes))

    def verify(self, receipt):
        size = receipt.get("size_bytes")
        if type(size) is not int or not 0 < size <= self.max_bytes:
            raise PilotError("Invalid receipt size or storage byte limit.")
        data = self.download(receipt["file_id"], size)
        if len(data) != size or digest(data) != receipt["sha256"]:
            raise PilotError("Stored bytes do not match the receipt.", code="hash_mismatch")
        return data

    def _allocate(self, parent, key):
        token = digest(encode([self.root, parent, key]))
        if token not in self.journal:
            # Recover remotely even when the local journal was lost. Never pick arbitrarily.
            result = checked(
                self.request(
                    "GET",
                    "/files",
                    params={
                        "q": f"'{drive_id(parent)}' in parents and trashed = false and "
                        f"appProperties has {{ key='xm_key' and value='{token}' }}",
                        "fields": "files(id),nextPageToken,incompleteSearch",
                        "pageSize": "2",
                    },
                )
            ).json()
            files = result.get("files")
            if (
                not isinstance(files, list)
                or len(files) > 1
                or result.get("nextPageToken")
                or result.get("incompleteSearch")
            ):
                raise PilotError("Ambiguous or incomplete Drive deduplication search.")
            if files:
                file_id = drive_id(files[0]["id"])
            else:
                generated = checked(
                    self.request(
                        "GET", "/files/generateIds", params={"count": "1", "space": "drive"}
                    )
                ).json()
                file_id = drive_id(generated["ids"][0])
            self.journal[token] = file_id
            self.store.write("drive-journal.private.json", self.journal)
        return drive_id(self.journal[token]), token

    def put(self, parent, key, name, data, mime):
        if data is not None and not 0 < len(data) <= self.max_bytes:
            raise PilotError("Upload is empty or exceeds the byte limit.", code="byte_limit")
        file_id, token = self._allocate(parent, key)
        props = {"xm_key": token}
        if data is not None:
            props["sha256"] = digest(data)
        metadata = {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "parents": [parent],
            "appProperties": props,
        }
        existing = self.metadata(file_id, missing_ok=True)
        if existing is None:
            if data is None:
                checked(self.request("POST", "/files", json=metadata), (200, 201, 409))
            else:
                start = self.http.post(
                    UPLOAD,
                    params={"uploadType": "resumable"},
                    json=metadata,
                    headers={
                        **self.headers(),
                        "X-Upload-Content-Type": mime,
                        "X-Upload-Content-Length": str(len(data)),
                    },
                )
                checked(start, (200, 201, 409))
                if start.status_code != 409:
                    location = start.headers.get("location", "")
                    url = urlsplit(location)
                    if (
                        url.scheme != "https"
                        or url.netloc != "www.googleapis.com"
                        or url.path != "/upload/drive/v3/files"
                        or not url.query
                    ):
                        raise PilotError("Unexpected Drive upload session URL.")
                    checked(
                        self.http.put(
                            location, content=data, headers={**self.headers(), "Content-Type": mime}
                        ),
                        (200, 201, 409),
                    )
            existing = self.metadata(file_id)
        if (
            existing.get("mimeType") != mime
            or parent not in existing.get("parents", [])
            or existing.get("appProperties") != props
        ):
            raise PilotError("Existing Drive object does not match the create-only intent.")
        if data is None:
            return file_id
        receipt = {
            "file_id": file_id,
            "sha256": digest(data),
            "size_bytes": len(data),
            "mime_type": mime,
            "name": existing["name"],
            "parent_id": parent,
        }
        self.verify(receipt)
        return receipt

    def folder(self, category):
        return self.put(self.root, "category:" + category, category, None, FOLDER)

    def save(self, category, name, data, mime):
        parent = self.folder(category)
        return self.put(parent, "bytes:" + digest(data), name, data, mime)
