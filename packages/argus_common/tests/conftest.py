import pytest

from argus_common.keys import SigningKey


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey.generate()
