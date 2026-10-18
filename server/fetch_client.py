"""Download NVIDIA's browser streaming client (@nvidia/ov-web-rtc) into server/vendor/.

The library is proprietary NVIDIA code, so it is not committed to this repo. The GPU
server fetches it from NVIDIA's npm registry and serves it to the web page.
"""

import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

REGISTRY = "https://edge.urm.nvidia.com/artifactory/api/npm/omniverse-client-npm/@nvidia%2fov-web-rtc"
OUTPUT = Path(__file__).with_name("vendor") / "ov-web-rtc.umd.js"


def main() -> None:
    with urllib.request.urlopen(REGISTRY, timeout=60) as response:
        metadata = json.load(response)
    version = sys.argv[1] if len(sys.argv) > 1 else metadata["dist-tags"]["latest"]
    tarball = metadata["versions"][version]["dist"]["tarball"]

    with urllib.request.urlopen(tarball, timeout=120) as response:
        archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
    script = archive.extractfile("package/dist/ov-web-rtc.umd.cjs").read()

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_bytes(script)
    print(f"Saved @nvidia/ov-web-rtc {version} to {OUTPUT}")


if __name__ == "__main__":
    main()
