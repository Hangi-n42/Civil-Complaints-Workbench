"""인증 없는 민원 시연 서버의 로컬 접속 경계."""
from ipaddress import ip_address
from urllib.parse import urlsplit


def is_loopback_host(host: str) -> bool:
    if host == 'localhost':
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_loopback_host(host: str) -> str:
    host = host.strip().lower()
    if not is_loopback_host(host):
        raise ValueError('로컬 시연은 API_HOST를 127.0.0.1, localhost 또는 ::1로 지정해야 합니다.')
    return host


def is_local_http_origin(value: str) -> bool:
    try:
        url = urlsplit(value)
        port = url.port
        return (url.scheme in ('http', 'https') and is_loopback_host(url.hostname or '')
                and url.username is None and url.password is None
                and not url.path and not url.query and not url.fragment
                and (port is None or 1 <= port <= 65535))
    except ValueError:
        return False
