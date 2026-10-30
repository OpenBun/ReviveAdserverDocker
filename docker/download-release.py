#!/usr/bin/env python3
"""Build-only official release fetcher; rejects non-HTTPS redirects and bad hashes."""
import argparse
import hashlib
from pathlib import Path
import re
import time
import urllib.error
import urllib.request


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if not new_url.startswith("https://"):
            raise ValueError("Unencrypted download redirect refused.")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def download(url, expected, output):
    if not re.fullmatch(
        r"https://download\.revive-adserver\.com/revive-adserver-[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?\.zip",
        url,
    ):
        raise ValueError("Only official HTTPS release ZIP URLs are accepted.")
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("A published SHA-256 checksum is required.")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Revive-Docker-Release-Download",
        "Referer": "https://www.revive-adserver.com/download/",
    })
    opener = urllib.request.build_opener(HTTPSOnly)
    for attempt in range(3):
        try:
            digest = hashlib.sha256()
            size = 0
            with opener.open(request, timeout=90) as response, Path(output).open("wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 128 * 1024 * 1024:
                        raise ValueError("Release exceeds download safety limit.")
                    digest.update(chunk)
                    stream.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError("Official release SHA-256 verification failed.")
            print("Official release ZIP SHA-256 verified.")
            return
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    download(args.url, args.sha256, args.output)