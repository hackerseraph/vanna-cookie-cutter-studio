"""Local-network model handoff to Bambuddy's File Manager API."""

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests


class BambuddyError(ValueError):
    pass


@dataclass(frozen=True)
class BambuddyConfig:
    url: str
    api_key: str
    folder_id: int | None

    @property
    def library_url(self) -> str:
        return f"{self.url}/files"


def load_config(path: Path) -> BambuddyConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BambuddyError("Cannot read bambuddy.local.json. Check the README setup, permissions and JSON syntax.") from error
    if not isinstance(data, dict):
        raise BambuddyError("Bambuddy configuration must be a JSON object.")
    url = data.get("url")
    if not isinstance(url, str) or any(ord(char) <= 32 for char in url):
        raise BambuddyError("Set url to Bambuddy's local HTTP or HTTPS address.")
    try:
        parts = urlsplit(url)
        port = parts.port
        address = ipaddress.ip_address(parts.hostname or "")
    except ValueError as error:
        raise BambuddyError("Use a private LAN or loopback IP address for Bambuddy, not a hostname.") from error
    local = address.is_loopback or any(address in ipaddress.ip_network(network) for network in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
    ))
    if (not local or parts.scheme not in {"http", "https"} or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or (port is not None and port == 0)):
        raise BambuddyError("Bambuddy URL must be a local HTTP(S) address without credentials, query or fragment.")
    api_key = data.get("api_key", "")
    if not isinstance(api_key, str) or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise BambuddyError("api_key must be a string without whitespace or control characters.")
    folder_id = data.get("folder_id")
    if folder_id is not None and (type(folder_id) is not int or folder_id < 1):
        raise BambuddyError("folder_id must be a positive integer or null.")
    return BambuddyConfig(url.rstrip("/"), api_key, folder_id)


class BambuddyClient:
    def __init__(self, config: BambuddyConfig):
        self.config = config

    def _request(self, method: str, endpoint: str, **kwargs) -> object:
        try:
            with requests.Session() as session:
                # Keep LAN credentials off configured system proxies and .netrc.
                session.trust_env = False
                if self.config.api_key:
                    session.headers["X-API-Key"] = self.config.api_key
                response = session.request(
                    method, f"{self.config.url}/api/v1/{endpoint}",
                    timeout=(5, 120), allow_redirects=False, **kwargs,
                )
                if response.status_code in {401, 403}:
                    raise BambuddyError("Bambuddy denied access. Check the API key and its library read/upload permissions.")
                if not 200 <= response.status_code < 300:
                    raise BambuddyError(
                        f"Bambuddy returned HTTP {response.status_code}. Check its URL, version, destination folder and server logs."
                    )
                try:
                    return response.json()
                except ValueError as error:
                    raise BambuddyError("Bambuddy returned invalid JSON. Check that the URL points to Bambuddy.") from error
        except requests.RequestException as error:
            # Do not surface remote response bodies or exception URLs with credentials.
            raise BambuddyError(
                "Could not reach Bambuddy. Check its LAN address, firewall and HTTPS certificate. "
                "If sending a file, check File Manager before retrying; the upload may have completed."
            ) from error

    def check_connection(self) -> None:
        folders = self._request("GET", "library/folders")
        if not isinstance(folders, list):
            raise BambuddyError("Unexpected Bambuddy folders response. Check the server version.")

    def upload(self, path: Path) -> dict:
        params = {"folder_id": self.config.folder_id} if self.config.folder_id is not None else {}
        with path.open("rb") as source:
            result = self._request(
                "POST", "library/files", params=params,
                files={"file": (path.name, source, "model/stl")},
            )
        if (not isinstance(result, dict) or type(result.get("id")) is not int
                or result["id"] < 1 or not isinstance(result.get("filename"), str)):
            raise BambuddyError("Unexpected Bambuddy upload response. Check File Manager before retrying.")
        return {"id": result["id"], "filename": result["filename"], "duplicate_of": result.get("duplicate_of")}
