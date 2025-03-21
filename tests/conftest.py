import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Iterator

import httpx
import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@dataclass
class MockIdP:
    public_key_pem: str
    private_key: rsa.RSAPrivateKey
    iss: str

    def encode_jwt(self, payload: dict) -> str:
        return jwt.encode(payload, self.private_key, algorithm="RS256")


@pytest.fixture(scope="session")
def oidc_mock_idp() -> Iterator[MockIdP]:
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_key = private_key.public_key()

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    yield MockIdP(
        public_key_pem=public_pem.decode("ascii"),
        private_key=private_key,
        iss="https://mock-idp.com",
    )


def _wlcg_token(oidc_mock_idp: MockIdP, scope: str) -> str:
    """Generate a WLCG-compatible bearer token.

    See:
    https://github.com/WLCG-AuthZ-WG/common-jwt-profile/blob/master/profile.md
    for a description of the profile.
    """
    not_before = int(time.time())
    issued_at = not_before
    expires = not_before + 4 * 3600
    token = {
        "wlcg.ver": "1.0",
        "sub": "test_subject",
        "aud": "https://wlcg.cern.ch/jwt/v1/any",
        "nbf": not_before,
        "scope": scope,
        "iss": oidc_mock_idp.iss,
        "exp": expires,
        "iat": issued_at,
        "jti": str(uuid.uuid4()),
        "client_id": "test_client_id",
    }
    return oidc_mock_idp.encode_jwt(token)


@pytest.fixture(scope="session")
def wlcg_read_header(oidc_mock_idp: MockIdP) -> dict[str, str]:
    """A WLCG token with read access to /

    storage.read: Read data. Only applies to “online” resources such as disk
    (as opposed to “nearline” such as tape where the stage authorization should be used in addition).
    """
    bt = _wlcg_token(oidc_mock_idp, "openid offline_access storage.read:/")
    return {"Authorization": f"Bearer {bt}"}


@pytest.fixture(scope="session")
def wlcg_create_header(oidc_mock_idp: MockIdP) -> dict[str, str]:
    """A WLCG token with create access to /

    storage.create: Upload data. This includes renaming files if the destination file does not
    already exist. This capability includes the creation of directories and subdirectories at
    the specified path, and the creation of any non-existent directories required to create the
    path itself. This authorization does not permit overwriting or deletion of stored data. The
    driving use case for a separate storage.create scope is to enable the stage-out of data from
    jobs on a worker node.

    TODO: does this include the ability to read the file after creation? For now, force it.
    """
    bt = _wlcg_token(
        oidc_mock_idp, "openid offline_access storage.read:/ storage.create:/"
    )
    return {"Authorization": f"Bearer {bt}"}


@pytest.fixture(scope="session")
def wlcg_modify_header(oidc_mock_idp: MockIdP) -> dict[str, str]:
    """A WLCG token with modify access to /

    storage.modify: Change data. This includes renaming files, creating new files, and writing data.
    This permission includes overwriting or replacing stored data in addition to deleting or truncating
    data. This is a strict superset of storage.create.
    """
    bt = _wlcg_token(
        oidc_mock_idp, "openid offline_access storage.read:/ storage.modify:/"
    )
    return {"Authorization": f"Bearer {bt}"}


@pytest.fixture(scope="session")
def setup_server(oidc_mock_idp: MockIdP):
    # Make sure we are in the right place: one up from tests/
    assert os.getcwd() == os.path.dirname(os.path.dirname(__file__))

    # see nginx/lua/config.lua for schema
    config = {
        "openidc_pubkey": oidc_mock_idp.public_key_pem,
        "receive_buffer_size": 4096,
        "performance_marker_threshold": 2 * 4096,
    }
    with open("nginx/lua/config.json", "w") as f:
        json.dump(config, f)

    # Build podman container
    subprocess.check_call(
        ["podman", "build", "-t", "nginx-webdav", "nginx", "-f", "nginx.dockerfile"]
    )

    yield

    # Clean up
    os.remove("nginx/lua/config.json")


@pytest.fixture(scope="module")
def nginx_server(setup_server) -> Iterator[str]:
    """A running nginx-webdav server for testing

    It's nice to have a module-scoped fixture for the server, so we can
    reduce the number of irrelevant log messages in the test output.
    """
    # Start podman container
    podman_cmd = [
        "podman",
        "run",
        "-d",
        "-p",
        "8080:8080",
        "-v",
        "./nginx/conf.d:/etc/nginx/conf.d",
        "-v",
        "./nginx/lua:/etc/nginx/lua",
        "--tmpfs",
        "/var/www/webdav:rw,size=100M,mode=1777",
    ]
    podman_cmd.append("nginx-webdav")
    container_id = subprocess.check_output(podman_cmd).decode().strip()

    subprocess.run(
        [
            "podman",
            "exec",
            "-i",
            container_id,
            "dd",
            "of=/var/www/webdav/hello.txt",
        ],
        input=b"Hello, world!",
        stderr=subprocess.DEVNULL,
        check=True,
    )

    # Wait for the container to start
    for _ in range(10):
        try:
            time.sleep(0.1)
            httpx.get("http://localhost:8080/")
            break
        except httpx.HTTPError:
            pass

    yield "http://localhost:8080/webdav"

    # Dump container logs
    subprocess.check_call(["podman", "logs", container_id])

    # Stop podman container and clean up
    subprocess.check_call(["podman", "stop", container_id], stdout=subprocess.DEVNULL)
    subprocess.check_call(["podman", "rm", container_id], stdout=subprocess.DEVNULL)
