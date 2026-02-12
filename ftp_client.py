# ftp_client.py
from __future__ import annotations

from dataclasses import dataclass
from ftplib import FTP
from io import BytesIO
from typing import List


@dataclass(frozen=True)
class FTPConfig:
    host: str
    port: int = 21
    username: str = ""
    password: str = ""
    base_dir: str = ""  # optioneel


class FTPManager:
    """
    Kleine, herbruikbare FTP helper.
    - download_bytes / download_text
    - list_files in een directory
    - join() om paden consistent te bouwen met/zonder base_dir
    """

    def __init__(self, cfg: FTPConfig, timeout: int = 30, passive: bool = True):
        self.cfg = cfg
        self.timeout = timeout
        self.passive = passive

    def join(self, *parts: str) -> str:
        base = (self.cfg.base_dir or "").strip().strip("/")
        clean_parts = [p.strip().strip("/") for p in parts if str(p).strip() != ""]
        if base == "":
            return "/".join(clean_parts) if clean_parts else ""
        return "/".join([base] + clean_parts)

    def _connect(self) -> FTP:
        ftp = FTP()
        ftp.connect(host=self.cfg.host, port=self.cfg.port, timeout=self.timeout)
        ftp.login(user=self.cfg.username, passwd=self.cfg.password)
        ftp.set_pasv(self.passive)
        return ftp

    def list_files(self, remote_dir: str) -> List[str]:
        ftp = self._connect()
        try:
            ftp.cwd(remote_dir)
            return ftp.nlst()
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    def download_bytes(self, remote_path: str) -> bytes:
        ftp = self._connect()
        bio = BytesIO()
        try:
            ftp.retrbinary(f"RETR {remote_path}", bio.write)
            return bio.getvalue()
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass

    def download_text(self, remote_path: str, encoding: str = "utf-8") -> str:
        return self.download_bytes(remote_path).decode(encoding, errors="replace")
