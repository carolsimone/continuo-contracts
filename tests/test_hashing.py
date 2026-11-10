import hashlib

import pytest

from continuo_contracts.errors import ContractsError, HashInputError
from continuo_contracts.hashing import fold3, fold4, sha256_hex, shared_fold, shared_hash

P, Q = b"P = 7\n", b"Q = -7\n"
S, C, X = "1" * 64, "2" * 64, "3" * 64


def test_sha256_hex():
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_hex(P) == "a29caeabca6d60ed30bb20afbb3f8363efca873bf674335174cd18b998d801ee"
    assert sha256_hex(Q) == "fbfbd7748767ad2711a691a6d637b7db423c3a49c8e1f0ddef0348f4df524084"


def test_the_three_and_four_part_folds():
    assert fold3(S, "", C) == "sha256:23208c80730ea2c9250f031ce7b06e45547cf8251da74b50b37a58e66f9e989a"
    assert fold4(S, "", C, "") == "sha256:31ac6d5f9ebddd3c3539ea0fa604293bbf30692abc8fc02665cb608b97431ddc"
    assert fold4(S, "", C, X) == "sha256:db25a1a38c261811c17554c6c34e7b99e11527b39098dbc545bcb4d5ed73b927"


def test_fold3_is_the_fold_topology_controller_checks_today():
    assert fold3("a", "b", "c") == "sha256:" + hashlib.sha256(b"a|b|c").hexdigest()


def test_shared_fold_folds_each_path_with_its_digest():
    units = {"lib/p.py": sha256_hex(P), "lib/q.py": sha256_hex(Q)}
    swapped = {"lib/p.py": sha256_hex(Q), "lib/q.py": sha256_hex(P)}
    assert shared_fold(units) == "80dceb20464b5e56cec8697e1fd8f2223f93b0a9ccfa14b5b61beb8e1ebe34a1"
    assert shared_fold(swapped) == "a79499240116387cbbdc824ed1d51dd80b7628c25ef0addee7465dac7ced8cfe"


def test_shared_hash_hashes_the_bytes_first():
    assert shared_hash({"lib/p.py": P, "lib/q.py": Q}) == "80dceb20464b5e56cec8697e1fd8f2223f93b0a9ccfa14b5b61beb8e1ebe34a1"
    assert shared_hash({}) == ""


def test_shared_fold_of_one_unit_and_of_none():
    assert shared_fold({"macro.finance.cents_to_eur": "0" * 64}) == (
        "3367b38e9df2622986841a7c6d7900e6ae0e9b4d7b81e0ac11f127ddf3aa7ea3")
    assert shared_fold({}) == ""


def test_shared_fold_ignores_insertion_order():
    assert shared_fold({"b": S, "a": C}) == shared_fold({"a": C, "b": S})


def test_shared_fold_encodes_a_non_ascii_path_as_utf8():
    assert shared_fold({"lib/m\u00fcnchen.py": sha256_hex(b"x = 1\n")}) == (
        "d29fa3a2e9a6faac568be060b35f64f805453d84ca68b7430e2b79369207101e")


@pytest.mark.parametrize(("key", "digest"), [
    ("lib/p.py", "sha256:" + "a" * 64),
    ("lib/p.py", "A" * 64),
    ("lib/p.py", "a" * 63),
    ("", "a" * 64),
    ("lib/p\x00.py", "a" * 64),
    ("lib/p\n.py", "a" * 64),
])
def test_shared_fold_refuses_a_key_or_digest_in_another_form(key, digest):
    with pytest.raises(HashInputError):
        shared_fold({key: digest})


def test_hash_input_error_is_a_contracts_error():
    assert issubclass(HashInputError, ContractsError)


def test_shared_fold_error_names_the_key():
    with pytest.raises(HashInputError, match=r"lib/p\.py"):
        shared_fold({"lib/p.py": "A" * 64})


def test_shared_fold_refuses_a_key_utf8_cannot_encode():
    with pytest.raises(HashInputError, match="UTF-8"):
        shared_fold({"lib/p" + chr(0xD800) + ".py": "a" * 64})
