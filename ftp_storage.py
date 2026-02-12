from ftplib import FTP
from io import BytesIO

def ftp_download_bytes(host, port, username, password, remote_path, timeout=30, passive=True) -> bytes:
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
            try:
                ftp.close()
            except Exception:
                pass

def ftp_download_text(host, port, username, password, remote_path, timeout=30, passive=True, encoding="utf-8") -> str:
    data = ftp_download_bytes(host, port, username, password, remote_path, timeout, passive)
    return data.decode(encoding, errors="replace")
