"""Download the GLIM author's small ROS2 example; validate archive paths first."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request

URL = "https://zenodo.org/api/records/7233945/files/os1_128_01_downsampled.tar.gz/content"
EXPECTED_BYTES = 426164255
EXPECTED_MD5 = "75ef57e84010217ab8a5f8da511ff2ea"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory outside Git")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    archive_path = args.output / "example.tar.gz"
    digest = hashlib.sha256()
    publisher_digest = hashlib.md5()
    total = 0
    with urllib.request.urlopen(URL, timeout=60) as response, archive_path.open("xb") as target:
        for block in iter(lambda: response.read(1 << 20), b""):
            total += len(block)
            if total > 1_000_000_000:
                raise ValueError("example archive exceeds download limit")
            target.write(block)
            digest.update(block)
            publisher_digest.update(block)
    manifest = dict(evidence_kind="PUBLIC_DATASET", url=URL, bytes=total,
                    sha256=digest.hexdigest(), publisher_md5=publisher_digest.hexdigest(),
                    expected_md5=EXPECTED_MD5, sensor="Ouster OS1-128 + IMU", na70_validated=False)
    valid = total == EXPECTED_BYTES and publisher_digest.hexdigest() == EXPECTED_MD5
    manifest["archive_status"] = "CHECKSUM_VERIFIED" if valid else "CHECKSUM_MISMATCH"
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if not valid:
        raise ValueError("public archive size/checksum mismatch; retained for diagnosis")
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > 4_000_000_000:
            raise ValueError("example expands beyond extraction limit")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f"unsafe archive member: {member.name}")
        archive.extractall(args.output / "bag", members=members)
    manifest["archive_status"] = "EXTRACTED"
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
