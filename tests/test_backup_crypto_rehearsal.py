"""Primitive rehearsal only: synthetic keys/data, never the private capture format.

No SSH connection, production keyring, privileged action or provider operation.
Loopback passphrases below are invented test data, NOT the owner pinentry workflow.
"""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile

import pytest

PASSPHRASE = b"synthetic-rehearsal-only\n"


def run(argv, *, data=None, check=True):
    return subprocess.run(argv, input=data, capture_output=True, check=check, timeout=30)


def decryption_succeeded(result):
    """Exit zero alone can also mean no encrypted data was processed."""
    statuses = {
        line.split()[1]
        for line in result.stdout.splitlines()
        if line.startswith(b"[GNUPG:] ") and len(line.split()) >= 2
    }
    return (
        result.returncode == 0
        and {b"DECRYPTION_OKAY", b"END_DECRYPTION"} <= statuses
        and not {b"DECRYPTION_FAILED", b"BADMDC", b"ERRMDC", b"ERROR", b"FAILURE"} & statuses
    )


@pytest.fixture(scope="module")
def crypto(tmp_path_factory):
    for executable in ("gpg", "gpgconf", "ssh-keygen"):
        assert shutil.which(executable), f"Crypto rehearsal requires {executable}"
    root = tmp_path_factory.mktemp("synthetic-recovery")
    homes = {name: root / name for name in ("source", "recovery", "public-only")}
    for home in homes.values():
        home.mkdir(mode=0o700)

    def gpg(name, *args, data=None, check=True, unlock=False):
        command = ["gpg", "--no-options", "--homedir", str(homes[name]), "--batch"]
        if unlock:
            command += ["--pinentry-mode", "loopback", "--passphrase-fd", "0"]
        return run(command + list(args), data=data, check=check)

    def clear(name):
        run(["gpgconf", "--homedir", str(homes[name]), "--kill", "gpg-agent"])

    try:
        gpg(
            "source",
            "--quick-generate-key",
            "Synthetic Recovery <fixture@example.invalid>",
            "ed25519",
            "cert",
            "0",
            unlock=True,
            data=PASSPHRASE,
        )
        listing = gpg("source", "--with-colons", "--list-keys").stdout.decode()
        primary = next(line.split(":")[9] for line in listing.splitlines() if line[:4] == "fpr:")
        gpg(
            "source",
            "--quick-add-key",
            primary,
            "cv25519",
            "encr",
            "0",
            unlock=True,
            data=PASSPHRASE,
        )
        public = gpg("source", "--export", primary).stdout
        gpg("public-only", "--import", data=public)
        # Export is kept solely in process memory; all material is synthetic.
        exported = gpg(
            "source", "--export-secret-keys", primary, unlock=True, data=PASSPHRASE
        ).stdout
        assert exported
        gpg("recovery", "--import", data=exported)
        del exported
        source_fprs = [
            line.split(":")[9]
            for line in gpg("source", "--with-colons", "--list-secret-keys")
            .stdout.decode()
            .splitlines()
            if line.startswith("fpr:")
        ]
        recovered_fprs = [
            line.split(":")[9]
            for line in gpg("recovery", "--with-colons", "--list-secret-keys")
            .stdout.decode()
            .splitlines()
            if line.startswith("fpr:")
        ]
        assert len(source_fprs) == 2 and source_fprs == recovered_fprs

        # Literal fixture inventory, deliberately NOT a proposed production manifest.
        payloads = {"posts/item": b"invented post", "media/item": b"invented media"}
        plain = io.BytesIO()
        with tarfile.open(fileobj=plain, mode="w") as archive:
            for directory in ("posts", "media", "empty"):
                info = tarfile.TarInfo(directory)
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            for name, data in payloads.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        plaintext = root / "synthetic.tar"
        plaintext.write_bytes(plain.getvalue())
        ciphertext = root / "synthetic.gpg"
        gpg(
            "public-only",
            "--trust-model",
            "always",
            "--recipient",
            primary,
            "--output",
            str(ciphertext),
            "--encrypt",
            str(plaintext),
        )
        keys = {}
        for name in ("signer", "other"):
            keys[name] = root / name
            run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(keys[name])])
        run(
            [
                "ssh-keygen",
                "-Y",
                "sign",
                "-f",
                str(keys["signer"]),
                "-n",
                "synthetic-backup",
                str(ciphertext),
            ]
        )
        allowed = {}
        for name, key in keys.items():
            allowed[name] = root / (name + "-allowed")
            allowed[name].write_text("fixture " + key.with_suffix(".pub").read_text())
        yield root, gpg, clear, ciphertext, allowed, payloads, plain.getvalue()
    finally:
        for name in homes:
            clear(name)


def test_exported_key_fresh_unlock_decrypts_exact_synthetic_inventory(crypto):
    root, gpg, clear, ciphertext, _, payloads, original = crypto
    clear("recovery")
    output = root / "recovered.tar"
    result = gpg(
        "recovery",
        "--status-fd",
        "1",
        "--output",
        str(output),
        "--decrypt",
        str(ciphertext),
        unlock=True,
        data=PASSPHRASE,
    )
    assert decryption_succeeded(result)
    recovered = output.read_bytes()
    assert hashlib.sha256(recovered).digest() == hashlib.sha256(original).digest()
    with tarfile.open(fileobj=io.BytesIO(recovered)) as archive:
        members = archive.getmembers()
        assert len(members) == 5
        assert {m.name for m in members if m.isdir()} == {"posts", "media", "empty"}
        assert {m.name for m in members if m.isfile()} == set(payloads)
        for name, expected in payloads.items():
            assert archive.extractfile(name).read() == expected


@pytest.mark.parametrize("failure", ["public-only", "wrong-passphrase", "truncated"])
def test_decrypt_failures_never_establish_recovery(crypto, failure):
    root, gpg, clear, ciphertext, _, _, _ = crypto
    clear("recovery")
    selected = ciphertext
    if failure == "truncated":
        selected = root / "truncated.gpg"
        selected.write_bytes(ciphertext.read_bytes()[:30])
    # Plaintext from a failed decrypt is never inspected or accepted.
    result = gpg(
        "public-only" if failure == "public-only" else "recovery",
        "--status-fd",
        "1",
        "--output",
        os.devnull,
        "--yes",
        "--decrypt",
        str(selected),
        unlock=True,
        data=b"wrong-synthetic-password\n" if failure == "wrong-passphrase" else PASSPHRASE,
        check=False,
    )
    assert not decryption_succeeded(result)


@pytest.mark.parametrize("failure", [None, "wrong-signer", "wrong-namespace", "altered"])
def test_signature_binds_key_namespace_and_exact_ciphertext(crypto, failure):
    _, _, _, ciphertext, allowed, _, _ = crypto
    data = ciphertext.read_bytes()
    if failure == "altered":
        data = data[:-1] + bytes([data[-1] ^ 1])
    result = run(
        [
            "ssh-keygen",
            "-Y",
            "verify",
            "-f",
            str(allowed["other" if failure == "wrong-signer" else "signer"]),
            "-I",
            "fixture",
            "-n",
            "wrong" if failure == "wrong-namespace" else "synthetic-backup",
            "-s",
            str(ciphertext) + ".sig",
        ],
        data=data,
        check=False,
    )
    assert (result.returncode == 0) == (failure is None)
