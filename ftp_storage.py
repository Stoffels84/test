from __future__ import annotations

from ftplib import FTP
from io import BytesIO
from typing import Optional


def ftp_download_bytes(
    host: str,
    port: int,
    username: str,
    password: str,
    remote_path: str,
    timeout: int = 30,
    passive: bool = True,
) -> bytes:
    """
    Download één bestand via FTP en geef de inhoud terug als bytes.
    """
    ftp = FTP()
    ftp.connect(host=host, port=port, timeout=timeout)
    ftp.login(user=username, passwd=password)
    ftp.set_pasv(passive)

    bio = BytesIO()

    try:
        ftp.retrbinary(f"RETR {remote_path}", bio.write)
        return bio.getvalue()
    finally:
        try:
            ftp.quit()
        except Exception:
            # Als de verbinding al weg is
            try:
                ftp.close()
            except Exception:
                pass
