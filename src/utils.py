import zipfile
import os
from typing import Optional


def unzip_file(
    zip_path: str, extract_to: str = ".", password: Optional[str] = None
) -> bool:
    os.makedirs(extract_to, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        pwd_bytes = password.encode("utf-8") if password else None
        zip_ref.extractall(path=extract_to, pwd=pwd_bytes)

    return True
