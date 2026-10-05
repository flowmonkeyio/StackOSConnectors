"""Native FTP action final-ack and atomic transfer proof, with a fake server."""

from __future__ import annotations

import asyncio
import ftplib
import json
import posixpath
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorError, ConnectorRequest
from stackos_connectors.actions.ftp import FtpActionConnector

from .catalog_fixture import client_for


def test_ftp_later_path_validation_preserves_completed_transfer(monkeypatch, tmp_path):
    import stackos_connectors.actions.ftp as ftp_module

    class MissingPwdAfterDownload(_FakeFTPTLS):
        instances: ClassVar[list] = []
        server_files: ClassVar[dict[str, bytes]] = {"/first.bin": b"completed"}
        downloaded = False

        def retrbinary(self, *args, **kwargs):
            result = super().retrbinary(*args, **kwargs)
            self.downloaded = True
            return result

        def pwd(self):
            return "" if self.downloaded else super().pwd()

    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", MissingPwdAfterDownload)
    first = tmp_path / "first.bin"
    request = _ftp_connector_request(
        operation="file.download",
        input_json={
            "items": [
                {"remote_path": "/first.bin", "local_path": str(first)},
                {"remote_path": "second.bin", "local_path": str(tmp_path / "second.bin")},
            ],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
    )
    with pytest.raises(ConnectorError) as caught:
        asyncio.run(FtpActionConnector().execute(request))
    assert first.read_bytes() == b"completed"
    assert caught.value.output_json["completed_count"] == 1
    assert str(first) in json.dumps(caught.value.output_json["completed"])
    assert caught.value.metadata_json.get("provider_executed") is not False
    assert "ftp-secret" not in json.dumps(caught.value.output_json)


def _ftp_connector_request(*, operation, input_json, progress_callback=None):
    return ConnectorRequest(
        connector="ftp",
        action_key=f"ftp.{operation}",
        operation=operation,
        input_json=input_json,
        config_json={},
        auth=ConnectorAuth(
            "ftp-password",
            {"password": "ftp-secret"},
            {
                "host": "ftp.example.test",
                "port": 21,
                "tls_mode": "explicit",
                "username": "deploy",
                "passive_mode": True,
            },
        ),
        options=CallOptions(progress_callback=progress_callback),
    )


@pytest.mark.asyncio
async def test_named_ftp_management_and_recursive_delete_order(monkeypatch):
    _patch_ftps(monkeypatch)
    _FakeFTPTLS.server_dirs = {"/", "/workspace", "/archive", "/tree", "/tree/nested"}
    _FakeFTPTLS.server_files = {
        "/workspace/delete.txt": b"delete",
        "/workspace/old.txt": b"rename",
        "/tree/nested/child.txt": b"child",
    }
    client = client_for("ftp")
    auth = _ftp_connector_request(operation="directory.list", input_json={}).auth
    await client.execute("ftp", "ftp.directory.create", {"remote_path": "/workspace/new"}, auth)
    await client.execute("ftp", "ftp.file.delete", {"remote_path": "/workspace/delete.txt"}, auth)
    await client.execute(
        "ftp",
        "ftp.path.rename",
        {"source_path": "/workspace/old.txt", "destination_path": "/archive/renamed.txt"},
        auth,
    )
    listing = await client.execute("ftp", "ftp.directory.list", {"remote_path": "/archive"}, auth)
    assert "renamed.txt" in str(listing.output_json)
    removed = await client.execute(
        "ftp", "ftp.directory.delete", {"remote_path": "/tree", "recursive": True}, auth
    )
    assert removed.output_json["deleted_paths"][-1] == {"remote_path": "/tree", "type": "directory"}
    assert _FakeFTPTLS.server_files["/archive/renamed.txt"] == b"rename"
    assert "/workspace/delete.txt" not in _FakeFTPTLS.server_files
    calls = [call for instance in _FakeFTPTLS.instances for call in instance.calls]
    assert (
        calls.index(("delete", "/tree/nested/child.txt"))
        < calls.index(("rmd", "/tree/nested"))
        < calls.index(("rmd", "/tree"))
    )


@pytest.mark.asyncio
async def test_named_ftp_rejects_command_injection_without_connection(monkeypatch):
    _patch_ftps(monkeypatch)
    from stackos_connectors import ValidationError

    with pytest.raises(ValidationError):
        await client_for("ftp").execute(
            "ftp",
            "ftp.file.delete",
            {"remote_path": "/safe\r\nDELE /other"},
            _ftp_connector_request(operation="file.delete", input_json={}).auth,
        )
    assert _FakeFTPTLS.instances == []


class _FakeFTP:
    instances: ClassVar[list[_FakeFTP]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def __init__(
        self, *, timeout: float | None = None, encoding: str = "utf-8", **_kwargs: Any
    ) -> None:
        self.timeout = timeout
        self.encoding = encoding
        self.cwd_path = "/"
        self.calls: list[tuple[Any, ...]] = []
        self.__class__.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances.clear()
        cls.server_dirs = {"/"}
        cls.server_files = {}
        cls.server_symlinks = set()
        cls.malicious_children = {}

    def _path(self, value: str) -> str:
        if value.startswith("/"):
            return posixpath.normpath(value)
        return posixpath.normpath(posixpath.join(self.cwd_path, value))

    def connect(self, host: str, port: int, timeout: float | None = None) -> str:
        self.calls.append(("connect", host, port, timeout))
        return "220 ready"

    def auth(self) -> str:
        self.calls.append(("auth",))
        return "234 AUTH TLS"

    def login(self, username: str, password: str) -> str:
        self.calls.append(("login", username, password))
        return "230 logged in"

    def prot_p(self) -> str:
        self.calls.append(("prot_p",))
        return "200 protected"

    def set_pasv(self, value: bool) -> None:
        self.calls.append(("set_pasv", value))

    def pwd(self) -> str:
        self.calls.append(("pwd",))
        return self.cwd_path

    def cwd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("cwd", resolved))
        if resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        self.cwd_path = resolved
        return "250 changed"

    def mkd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("mkd", resolved))
        parent = posixpath.dirname(resolved) or "/"
        if parent not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 parent unavailable")
        if (
            resolved in self.__class__.server_dirs
            or resolved in self.__class__.server_files
            or resolved in self.__class__.server_symlinks
        ):
            raise ftplib.error_perm("550 path already exists")
        self.__class__.server_dirs.add(resolved)
        return resolved

    def delete(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("delete", resolved))
        if resolved in self.__class__.server_files:
            del self.__class__.server_files[resolved]
            return "250 deleted"
        if resolved in self.__class__.server_symlinks:
            self.__class__.server_symlinks.remove(resolved)
            return "250 deleted"
        raise ftplib.error_perm("550 file unavailable")

    def rmd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("rmd", resolved))
        if resolved == "/" or resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        prefix = resolved.rstrip("/") + "/"
        if any(
            item.startswith(prefix)
            for item in (
                *self.__class__.server_dirs,
                *self.__class__.server_files,
                *self.__class__.server_symlinks,
            )
        ):
            raise ftplib.error_perm("550 directory not empty")
        self.__class__.server_dirs.remove(resolved)
        return "250 removed"

    def rename(self, fromname: str, toname: str) -> str:
        source = self._path(fromname)
        destination = self._path(toname)
        self.calls.extend([("rnfr", source), ("rnto", destination)])
        destination_parent = posixpath.dirname(destination) or "/"
        if destination_parent not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 destination parent unavailable")
        if (
            destination in self.__class__.server_dirs
            or destination in self.__class__.server_files
            or destination in self.__class__.server_symlinks
        ):
            raise ftplib.error_perm("550 destination exists")
        if source in self.__class__.server_files:
            self.__class__.server_files[destination] = self.__class__.server_files.pop(source)
            return "250 renamed"
        if source in self.__class__.server_symlinks:
            self.__class__.server_symlinks.remove(source)
            self.__class__.server_symlinks.add(destination)
            return "250 renamed"
        if source not in self.__class__.server_dirs or source == "/":
            raise ftplib.error_perm("550 source unavailable")
        source_prefix = source.rstrip("/") + "/"
        directory_moves = {
            path: destination + path[len(source) :]
            for path in self.__class__.server_dirs
            if path == source or path.startswith(source_prefix)
        }
        file_moves = {
            path: destination + path[len(source) :]
            for path in self.__class__.server_files
            if path.startswith(source_prefix)
        }
        symlink_moves = {
            path: destination + path[len(source) :]
            for path in self.__class__.server_symlinks
            if path.startswith(source_prefix)
        }
        for path in directory_moves:
            self.__class__.server_dirs.remove(path)
        self.__class__.server_dirs.update(directory_moves.values())
        for path, target in file_moves.items():
            self.__class__.server_files[target] = self.__class__.server_files.pop(path)
        for path in symlink_moves:
            self.__class__.server_symlinks.remove(path)
        self.__class__.server_symlinks.update(symlink_moves.values())
        return "250 renamed"

    def storbinary(
        self, command: str, file_obj: Any, blocksize: int = 8192, callback: Any | None = None
    ) -> str:
        _, raw_path = command.split(" ", 1)
        path = self._path(raw_path)
        self.calls.append(("storbinary", path))
        chunks: list[bytes] = []
        while chunk := file_obj.read(blocksize):
            chunks.append(chunk)
            if callback is not None:
                callback(chunk)
        self.__class__.server_files[path] = b"".join(chunks)
        return "226 stored"

    def retrbinary(self, command: str, callback: Any, blocksize: int = 8192) -> str:
        del blocksize
        _, raw_path = command.split(" ", 1)
        path = self._path(raw_path)
        self.calls.append(("retrbinary", path))
        try:
            payload = self.__class__.server_files[path]
        except KeyError as exc:
            raise ftplib.error_perm("550 file unavailable") from exc
        callback(payload)
        return "226 retrieved"

    def mlsd(
        self, path: str = "", facts: list[str] | None = None
    ) -> Iterator[tuple[str, dict[str, str]]]:
        del facts
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("mlsd", resolved))
        if resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        for name in self.__class__.malicious_children.get(resolved, []):
            yield (name, {"type": "file", "size": "4", "modify": "20260715000000"})
        prefix = resolved.rstrip("/") + "/"
        children: dict[str, dict[str, str]] = {}
        for directory in self.__class__.server_dirs:
            if directory == resolved or not directory.startswith(prefix):
                continue
            remainder = directory[len(prefix) :]
            if "/" not in remainder:
                children[remainder] = {"type": "dir", "modify": "20260715000000"}
        for file_path, payload in self.__class__.server_files.items():
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            if "/" not in remainder:
                children[remainder] = {
                    "type": "file",
                    "size": str(len(payload)),
                    "modify": "20260715000000",
                }
        for link_path in self.__class__.server_symlinks:
            if not link_path.startswith(prefix):
                continue
            remainder = link_path[len(prefix) :]
            if "/" not in remainder:
                children[remainder] = {"type": "OS.unix=slink", "modify": "20260715000000"}
        yield from sorted(children.items())

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        return [name for name, _facts in self.mlsd(resolved)]

    def size(self, path: str) -> int | None:
        resolved = self._path(path)
        self.calls.append(("size", resolved))
        if resolved in self.__class__.server_files:
            return len(self.__class__.server_files[resolved])
        raise ftplib.error_perm("550 file unavailable")

    def sendcmd(self, command: str) -> str:
        verb, raw_path = command.split(" ", 1)
        assert verb == "MLST"
        path = self._path(raw_path)
        self.calls.append(("mlst", path))
        if path in self.__class__.server_dirs:
            return f"250-Listing\n type=dir; {posixpath.basename(path)}\n250 End"
        if path in self.__class__.server_files:
            size = len(self.__class__.server_files[path])
            return f"250-Listing\n type=file;size={size}; {posixpath.basename(path)}\n250 End"
        if path in self.__class__.server_symlinks:
            return f"250-Listing\n type=OS.unix=slink; {posixpath.basename(path)}\n250 End"
        raise ftplib.error_perm("550 path unavailable")

    def quit(self) -> str:
        self.calls.append(("quit",))
        return "221 bye"

    def close(self) -> None:
        self.calls.append(("close",))


class _FakeFTPTLS(_FakeFTP):
    instances: ClassVar[list[_FakeFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}


class _FallbackFTPTLS(_FakeFTPTLS):
    instances: ClassVar[list[_FallbackFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def mlsd(
        self, path: str = "", facts: list[str] | None = None
    ) -> Iterator[tuple[str, dict[str, str]]]:
        del path, facts
        raise ftplib.error_perm("500 MLSD unsupported")

    def sendcmd(self, command: str) -> str:
        self.calls.append(("mlst_unsupported", command))
        raise ftplib.error_perm("500 MLST unsupported")

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        prefix = resolved.rstrip("/") + "/"
        children = {
            item
            for item in (*self.__class__.server_dirs, *self.__class__.server_files)
            if item != resolved and item.startswith(prefix) and ("/" not in item[len(prefix) :])
        }
        return sorted(children)

    def retrlines(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("LIST must never be used or parsed")


class _MlstFallbackFTPTLS(_FakeFTPTLS):
    instances: ClassVar[list[_MlstFallbackFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def mlsd(
        self, path: str = "", facts: list[str] | None = None
    ) -> Iterator[tuple[str, dict[str, str]]]:
        del path, facts
        raise ftplib.error_perm("500 MLSD unsupported")

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        prefix = resolved.rstrip("/") + "/"
        children = {
            item
            for item in (
                *self.__class__.server_dirs,
                *self.__class__.server_files,
                *self.__class__.server_symlinks,
            )
            if item != resolved and item.startswith(prefix) and ("/" not in item[len(prefix) :])
        }
        return sorted(children)


class _FallbackCycleFTPTLS(_FallbackFTPTLS):
    instances: ClassVar[list[_FallbackCycleFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/", "/cycle"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def cwd(self, path: str) -> str:
        resolved = self._path(path)
        self.calls.append(("cwd", resolved))
        if resolved == "/cycle/loop":
            self.cwd_path = "/cycle"
            return "250 changed"
        if resolved not in self.__class__.server_dirs:
            raise ftplib.error_perm("550 directory unavailable")
        self.cwd_path = resolved
        return "250 changed"

    def nlst(self, path: str = "") -> list[str]:
        resolved = self._path(path or self.cwd_path)
        self.calls.append(("nlst", resolved))
        return ["/cycle/loop"] if resolved == "/cycle" else []


class _FailingDownloadFTPTLS(_FakeFTPTLS):
    instances: ClassVar[list[_FailingDownloadFTPTLS]] = []
    server_dirs: ClassVar[set[str]] = {"/"}
    server_files: ClassVar[dict[str, bytes]] = {}
    server_symlinks: ClassVar[set[str]] = set()
    malicious_children: ClassVar[dict[str, list[str]]] = {}

    def retrbinary(self, command: str, callback: Any, blocksize: int = 8192) -> str:
        del blocksize
        _, raw_path = command.split(" ", 1)
        path = self._path(raw_path)
        self.calls.append(("retrbinary", path))
        callback(b"partial")
        raise ftplib.error_temp("426 transfer aborted")


def _patch_ftps(monkeypatch: pytest.MonkeyPatch) -> None:
    import stackos_connectors.actions.ftp as ftp_module

    _FakeFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FakeFTPTLS)


def _assert_transfer_progress(
    snapshots: list[dict[str, Any]], *, source_path: str, target_path: str, expected_bytes: int
) -> None:
    transferring = [item for item in snapshots if item.get("phase") == "transferring"]
    assert transferring
    assert transferring[-1]["current_source_path"] == source_path
    assert transferring[-1]["current_target_path"] == target_path
    assert transferring[-1]["bytes_transferred"] == expected_bytes
    assert [item["bytes_transferred"] for item in snapshots] == sorted(
        item["bytes_transferred"] for item in snapshots
    )
    for count_key in ("completed_count", "skipped_count", "failed_count"):
        values = [item[count_key] for item in snapshots]
        assert values == sorted(values)


def test_ftp_upload_progress_waits_for_final_server_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stackos_connectors.actions.ftp as ftp_module

    class _FinalReplyFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_FinalReplyFTPTLS]] = []
        bytes_sent = threading.Event()
        allow_final_reply = threading.Event()

        def storbinary(
            self, command: str, file_obj: Any, blocksize: int = 8192, callback: Any | None = None
        ) -> str:
            _, raw_path = command.split(" ", 1)
            path = self._path(raw_path)
            self.calls.append(("storbinary", path))
            chunks: list[bytes] = []
            while chunk := file_obj.read(blocksize):
                chunks.append(chunk)
                if callback is not None:
                    callback(chunk)
            self.__class__.server_files[path] = b"".join(chunks)
            self.__class__.bytes_sent.set()
            self.__class__.allow_final_reply.wait()
            return "226 stored"

    _FinalReplyFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FinalReplyFTPTLS)
    source = tmp_path / "payload.bin"
    payload = b"raw-file-content-must-not-leak"
    source.write_bytes(payload)
    snapshots: list[dict[str, Any]] = []
    request = _ftp_connector_request(
        operation="file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/payload.bin"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        progress_callback=snapshots.append,
    )

    async def execute() -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
        task = asyncio.create_task(FtpActionConnector().execute(request))
        await asyncio.to_thread(_FinalReplyFTPTLS.bytes_sent.wait)
        done_before_final_reply = task.done()
        progress_before_final_reply = list(snapshots)
        _FinalReplyFTPTLS.allow_final_reply.set()
        result = await task
        return (done_before_final_reply, progress_before_final_reply, result.output_json)

    done_before_reply, progress_before_reply, output = asyncio.run(execute())
    assert done_before_reply is False
    assert all(item["completed_count"] == 0 for item in progress_before_reply)
    _assert_transfer_progress(
        progress_before_reply,
        source_path=str(source),
        target_path="/payload.bin",
        expected_bytes=len(payload),
    )
    assert output["completed_count"] == 1
    rendered = json.dumps(snapshots)
    assert "raw-file-content-must-not-leak" not in rendered
    assert "ftp-secret" not in rendered


def test_ftp_download_reports_sanitized_monotonic_progress_and_keeps_atomic_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ftps(monkeypatch)
    payload = b"download-file-content-must-not-leak"
    _FakeFTPTLS.server_files = {"/download.bin": payload}
    destination = tmp_path / "download.bin"
    snapshots: list[dict[str, Any]] = []
    request = _ftp_connector_request(
        operation="file.download",
        input_json={
            "items": [{"remote_path": "/download.bin", "local_path": str(destination)}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        progress_callback=snapshots.append,
    )
    result = asyncio.run(FtpActionConnector().execute(request))
    _assert_transfer_progress(
        snapshots,
        source_path="/download.bin",
        target_path=str(destination),
        expected_bytes=len(payload),
    )
    assert destination.read_bytes() == payload
    assert not list(tmp_path.glob(".download.bin.stackos-ftp-*.part"))
    assert result.output_json["completed_count"] == 1
    rendered = json.dumps(snapshots)
    assert "download-file-content-must-not-leak" not in rendered
    assert "ftp-secret" not in rendered


def test_ftp_upload_full_bytes_then_final_error_remains_outcome_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stackos_connectors.actions.ftp as ftp_module

    class _FinalErrorFTPTLS(_FakeFTPTLS):
        instances: ClassVar[list[_FinalErrorFTPTLS]] = []

        def storbinary(
            self, command: str, file_obj: Any, blocksize: int = 8192, callback: Any | None = None
        ) -> str:
            _, raw_path = command.split(" ", 1)
            path = self._path(raw_path)
            payload = file_obj.read()
            self.__class__.server_files[path] = payload
            if callback is not None:
                callback(payload)
            raise ftplib.error_temp("451 final transfer acknowledgement failed")

    _FinalErrorFTPTLS.reset()
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _FinalErrorFTPTLS)
    payload = b"all-bytes-were-sent"
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    snapshots: list[dict[str, Any]] = []
    request = _ftp_connector_request(
        operation="file.upload",
        input_json={
            "items": [{"local_path": str(source), "remote_path": "/payload.bin"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
        progress_callback=snapshots.append,
    )
    with pytest.raises(ConnectorError) as excinfo:
        asyncio.run(FtpActionConnector().execute(request))
    failure = excinfo.value.output_json["failed"][0]
    assert failure["attempted_bytes"] == len(payload)
    assert failure["outcome_unknown"] is True
    assert failure["retry_safe"] is False
    assert excinfo.value.output_json["completed_count"] == 0
