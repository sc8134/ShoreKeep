import pytest

from backend.security import UnsafeURLError, assert_public_http_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/file.mp4",
        "http://10.1.2.3/file.mp4",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/file.mp4",
        "http://service.internal/file.mp4",
        "ftp://example.com/file.mp4",
        "file:///etc/passwd",
    ],
)
def test_blocks_non_public_targets(url):
    with pytest.raises(UnsafeURLError):
        assert_public_http_url(url)


def test_allows_public_address():
    assert_public_http_url("https://93.184.216.34/file.mp4")
