"""Ag katmani: zaman asimlari, IPv4 onceligi, vekil sunucu cozumu, hata ayrimi.

Kurum aginda yasanan somut kusur bu modulun sebebi: uygulama acildiginda
"Baglantiyi sina" otuz saniyede bir yeniden deneyip doksan saniye sonra
anlamsiz bir hata veriyordu. Log'da yalnizca urllib3'un
"Starting new HTTPS connection (1)(2)(3)" satirlari vardi; yani TCP hic
kurulamiyordu. Uc ayri sebep ayni goruntuye yol aciyor:

* Python AAAA (IPv6) kaydini once deneyip zaman asimina dusuyor, tarayici
  IPv4'e iniyor ve calisiyor.
* Kurum vekil sunucu istiyor; `requests` yalnizca ortam degiskenlerini gorur,
  Windows'un kayit defterindeki `ProxyServer` ile PAC dosyasini gormez.
* Guvenlik duvari kapiyi kapatmistir.

Burada: baglanti zaman asimi okuma zaman asimindan ayrilir, baglanti
hatasinda yeniden deneme yapilmaz (kullanici doksan saniye beklemesin),
IPv4 adresleri once denenir ve sistem vekil sunucusu yedege alinir.
"""

from __future__ import annotations

import socket
import ssl
import urllib.request
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlparse

import requests
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import (
    ConnectTimeoutError,
    NameResolutionError,
    NewConnectionError,
)
from urllib3.util.connection import _set_socket_options
from urllib3.util.timeout import _DEFAULT_TIMEOUT

from .settings_store import PROXY_DIRECT, PROXY_MANUAL, PROXY_SYSTEM

# Baglanti 10 sn icinde kurulmali; cevap icin 30 sn beklenir.
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
DEFAULT_TIMEOUT: tuple[float, float] = (CONNECT_TIMEOUT, READ_TIMEOUT)

# Teshis ekrani her adimi kisa tutar: kullanici ekran basinda bekliyor.
PROBE_TIMEOUT = 5.0

# Hata metinlerinde kurum adresi gecmesin: log paylasilabilir kalsin.
SERVER_LABEL = "Jira sunucusu"


# --- adres sirasi ---------------------------------------------------------


def order_addresses(infos: Iterable[Any], ipv4_first: bool = True) -> list[Any]:
    """`getaddrinfo` sonucunu siralar: IPv4 kayitlari one alinir.

    Siralama kararli: kendi icinde IPv4 ve IPv6 kayitlari isletim sisteminin
    verdigi sirayi korur.
    """
    items = list(infos)
    if not ipv4_first:
        return items
    ipv4 = [item for item in items if item[0] == socket.AF_INET]
    other = [item for item in items if item[0] != socket.AF_INET]
    return ipv4 + other


def resolve(
    host: str,
    port: int,
    *,
    ipv4_first: bool = True,
    getaddrinfo: Callable[..., list[Any]] | None = None,
) -> list[Any]:
    """Ada ait tum adresler, IPv4 onde."""
    lookup = getaddrinfo or socket.getaddrinfo
    infos = lookup(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    return order_addresses(infos, ipv4_first)


def addresses_of(infos: Iterable[Any]) -> list[str]:
    """Adres listesini gosterilebilir metne cevirir (IP'ler kullanicinin kendi agi)."""
    result: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr, tuple):
            result.append(str(sockaddr[0]))
    return result


def family_name(family: int) -> str:
    if family == socket.AF_INET:
        return "IPv4"
    if family == socket.AF_INET6:
        return "IPv6"
    return str(family)


def create_connection(
    address: tuple[str, int],
    timeout: Any = _DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
    socket_options: Sequence[tuple[int, int, Any]] | None = None,
    *,
    ipv4_first: bool = True,
) -> socket.socket:
    """urllib3'un `create_connection` kopyasi; tek farki adres sirasi.

    urllib3 `allowed_gai_family()` ile AF_UNSPEC ister ve isletim sisteminin
    verdigi sirayla dener. Kurum agindaki kusur tam burada: AAAA kaydi
    donuyor, IPv6 yolu sessizce dusuyor ve IPv4 denemesine sira gelene kadar
    kullanici bekliyor.
    """
    host, port = address
    if host.startswith("["):
        host = host.strip("[]")

    infos = resolve(host, port, ipv4_first=ipv4_first)

    err: BaseException | None = None
    for family, socktype, proto, _canonname, sockaddr in infos:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            _set_socket_options(sock, list(socket_options or []))
            if timeout is not _DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()

    if err is not None:
        raise err
    raise OSError("getaddrinfo bos liste dondurdu")


class _Ipv4FirstConnection:
    """`_new_conn` icin IPv4 oncelikli baglanti; gerisi urllib3'un kendi kodu."""

    def _new_conn(self) -> socket.socket:  # type: ignore[override]
        try:
            return create_connection(
                (self._dns_host, self.port),  # type: ignore[attr-defined]
                self.timeout,  # type: ignore[attr-defined]
                source_address=self.source_address,  # type: ignore[attr-defined]
                socket_options=self.socket_options,  # type: ignore[attr-defined]
                ipv4_first=True,
            )
        except socket.gaierror as exc:
            # type: ignore[attr-defined,arg-type]
            raise NameResolutionError(self.host, self, exc) from exc
        except socket.timeout as exc:
            raise ConnectTimeoutError(
                self,
                f"Connection to {self.host} timed out. "  # type: ignore[attr-defined]
                f"(connect timeout={self.timeout})",  # type: ignore[attr-defined]
            ) from exc
        except OSError as exc:
            message = f"Failed to establish a new connection: {exc}"
            raise NewConnectionError(self, message) from exc  # type: ignore[arg-type]


class Ipv4FirstHTTPConnection(_Ipv4FirstConnection, HTTPConnection):
    pass


class Ipv4FirstHTTPSConnection(_Ipv4FirstConnection, HTTPSConnection):
    pass


class Ipv4FirstHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = Ipv4FirstHTTPConnection


class Ipv4FirstHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = Ipv4FirstHTTPSConnection


POOL_CLASSES: dict[str, type] = {
    "http": Ipv4FirstHTTPConnectionPool,
    "https": Ipv4FirstHTTPSConnectionPool,
}


class Ipv4FirstAdapter(requests.adapters.HTTPAdapter):
    """Havuz yoneticisine kendi baglanti sinifimizi taktiran adaptor."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = dict(POOL_CLASSES)

    def proxy_manager_for(self, proxy: str, **kwargs: Any) -> Any:
        manager = super().proxy_manager_for(proxy, **kwargs)
        # Vekil sunucunun kendi adi da IPv4 onceligiyle cozulsun.
        manager.pool_classes_by_scheme = dict(POOL_CLASSES)
        return manager


def build_session(ipv4_first: bool = True, trust_env: bool = True) -> requests.Session:
    """Jira icin oturum: istenirse IPv4 oncelikli adaptorle.

    `trust_env=False` ortam degiskenlerini (https_proxy dahil) tumden kapatir;
    "dogrudan baglan" kipinde kurumsal vekil sunucu devreye girmesin diye.
    """
    session = requests.Session()
    session.trust_env = trust_env
    if ipv4_first:
        adapter = Ipv4FirstAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    return session


# --- vekil sunucu ---------------------------------------------------------


def system_proxies(getproxies: Callable[[], dict[str, str]] | None = None) -> dict[str, str]:
    """Isletim sisteminin bildirdigi vekil sunucular (Windows'ta kayit defteri dahil).

    `urllib.request.getproxies()` once ortam degiskenlerine, sonra Windows
    kayit defterindeki `ProxyServer` degerine bakar. `requests` ikincisini hic
    gormez; proxy alanlari bos birakildiginda yedek olarak buraya duseriz.
    """
    lookup = getproxies or urllib.request.getproxies
    try:
        raw = lookup() or {}
    except Exception:  # noqa: BLE001 - kayit defteri okunamazsa yedeksiz devam
        return {}
    result: dict[str, str] = {}
    for scheme in ("http", "https"):
        value = str(raw.get(scheme) or "").strip()
        if value:
            result[scheme] = value
    return result


def _registry_pac() -> str:
    """Windows kayit defterindeki `AutoConfigURL` (PAC) degeri; yoksa bos."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AutoConfigURL")
        return str(value or "").strip()
    except OSError:
        return ""


def pac_url(reader: Callable[[], str] | None = None) -> str:
    """PAC/WPAD adresi tanimliysa dondurur.

    PAC cozmeye kalkismiyoruz: JavaScript yorumlamak, WPAD kesfi ve kurum
    kurallarini taklit etmek demek. Kullaniciya "sisteminde PAC var" deyip
    dogru vekil sunucuyu ag yoneticisinden ogrenmesini soylemek yeterli.
    """
    lookup = reader or _registry_pac
    try:
        return lookup() or ""
    except Exception:  # noqa: BLE001
        return ""


def bypasses_proxy(host: str, no_proxy: str) -> bool:
    """`no_proxy` listesi bu adresi vekil sunucudan muaf tutuyor mu?

    Kurum ici Jira cogu zaman vekil sunucunun arkasinda degildir; listeye
    `.kurum.local` yazan kullanici dogrudan cikmak ister.
    """
    name = (host or "").strip().lower().strip("[]")
    if not name:
        return False
    for raw in (no_proxy or "").split(","):
        entry = raw.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return True
        entry = entry.lstrip(".")
        if not entry:
            continue
        if name == entry or name.endswith("." + entry):
            return True
    return False


# requests'te bos dize "bu sema icin vekil sunucu yok" demektir ve ortamdan
# gelen degerin setdefault ile uzerine yazilmasini da engeller.
NO_PROXY_MAP: dict[str, str] = {"http": "", "https": ""}


def effective_proxies(
    host: str,
    *,
    mode: str = PROXY_SYSTEM,
    proxy_http: str = "",
    proxy_https: str = "",
    no_proxy: str = "",
    system: Callable[[], dict[str, str]] | None = None,
) -> tuple[dict[str, str], str]:
    """Bu adres icin gecerli vekil sunucu haritasi ve nereden geldigi.

    Kipler:

    * `direct`: vekil sunucu hic kullanilmaz. Kurum makinesinde `https_proxy`
      kurumsal vekile bakiyor olsa bile ic agdaki Jira'ya dogrudan cikilir.
    * `manual`: yalnizca kullanicinin yazdigi alanlar.
    * `system`: alanlar doluysa onlar, bos ise isletim sisteminin bildirdigi
      (ortam degiskeni ya da Windows kayit defteri) deger.

    Kaynak degerleri: `dogrudan`, `atlandi` (no_proxy kapsiyor), `ayar`,
    `sistem`, `yok`.
    """
    if mode == PROXY_DIRECT:
        return dict(NO_PROXY_MAP), "dogrudan"

    if no_proxy and bypasses_proxy(host, no_proxy):
        return {**NO_PROXY_MAP, "no_proxy": no_proxy}, "atlandi"

    configured: dict[str, str] = {}
    if proxy_http:
        configured["http"] = proxy_http
    if proxy_https:
        configured["https"] = proxy_https

    if configured:
        proxies = configured
        source = "ayar"
    elif mode == PROXY_MANUAL:
        # Elle kipte sistem yedegi yok: kullanici alanlari bos biraktiysa
        # "vekil sunucu kullanma" demis sayilir.
        proxies = {}
        source = "yok"
    else:
        proxies = dict((system or system_proxies)())
        source = "sistem" if proxies else "yok"

    if no_proxy:
        proxies["no_proxy"] = no_proxy
    return proxies, source


def proxies_for_config(config: Any, host: str) -> tuple[dict[str, str], str]:
    """`JiraConfig` alanlarindan vekil sunucu haritasi uretir."""
    return effective_proxies(
        host,
        mode=getattr(config, "proxy_mode", PROXY_SYSTEM),
        proxy_http=getattr(config, "proxy_http", ""),
        proxy_https=getattr(config, "proxy_https", ""),
        no_proxy=getattr(config, "no_proxy", ""),
    )


def proxy_for(url: str, proxies: dict[str, str] | None) -> str:
    """Verilen adres icin kullanilacak vekil sunucu adresi (yoksa bos)."""
    if not proxies:
        return ""
    scheme = (urlparse(url).scheme or "https").lower()
    return str(proxies.get(scheme) or "").strip()


# --- hata ayrimi ----------------------------------------------------------


def redact_host(text: str, host: str) -> str:
    """Metinde gecen sunucu adini etiketle degistirir."""
    cleaned = str(text or "")
    if host:
        cleaned = cleaned.replace(host, SERVER_LABEL)
    return cleaned


def _chain(exc: BaseException) -> list[BaseException]:
    """Istisna zinciri: sebep, baglam ve requests'in sardigi urllib3 hatasi."""
    seen: list[BaseException] = []
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if current is None or any(current is item for item in seen):
            continue
        seen.append(current)
        for candidate in (
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
            getattr(current, "reason", None),
        ):
            if isinstance(candidate, BaseException):
                stack.append(candidate)
        for arg in getattr(current, "args", ()) or ():
            if isinstance(arg, BaseException):
                stack.append(arg)
    return seen


CONNECT_TIMEOUT_MESSAGE = (
    f"{int(CONNECT_TIMEOUT)} saniye içinde {SERVER_LABEL} ile TCP bağlantısı kurulamadı. "
    "Ağınız bir vekil sunucu istiyor olabilir: Ayarlar → Ağ. "
    "Nerede takıldığını görmek için Teşhis düğmesini kullanın."
)
CONNECTION_REFUSED_MESSAGE = (
    f"{SERVER_LABEL} bağlantıyı reddetti: adres doğru ama kapı kapalı. "
    "Adresteki kapı numarasını ve http/https seçimini doğrulayın; Teşhis düğmesi ayrıntıyı verir."
)
DNS_MESSAGE = (
    f"{SERVER_LABEL} adı çözülemedi (DNS). Adresi yeniden yazın, VPN gerekiyorsa bağlanın; "
    "Teşhis düğmesi hangi adreslerin döndüğünü gösterir."
)
NETWORK_MESSAGE = (
    f"{SERVER_LABEL} ile bağlantı kurulamadı. Ağ, güvenlik duvarı ya da vekil sunucu "
    "engelliyor olabilir: Teşhis düğmesi hangi adımda durduğunu söyler."
)


def classify_connection_error(exc: BaseException) -> tuple[str, str]:
    """Baglanti hatasini kod ve Turkce mesaja cevirir.

    Kod degerleri: `connect_timeout`, `connection_refused`, `dns_error`,
    `network_error`. Mesajda sunucu adi gecmez.
    """
    chain = _chain(exc)

    def has(*types: type) -> bool:
        return any(isinstance(item, types) for item in chain)

    if has(socket.gaierror, NameResolutionError):
        return "dns_error", DNS_MESSAGE
    if has(ConnectionRefusedError):
        return "connection_refused", CONNECTION_REFUSED_MESSAGE
    if has(requests.exceptions.ConnectTimeout, ConnectTimeoutError, socket.timeout):
        return "connect_timeout", CONNECT_TIMEOUT_MESSAGE

    text = " ".join(str(item) for item in chain).lower()
    if "name or service not known" in text or "nodename nor servname" in text:
        return "dns_error", DNS_MESSAGE
    if "refused" in text:
        return "connection_refused", CONNECTION_REFUSED_MESSAGE
    if "timed out" in text or "timeout" in text:
        return "connect_timeout", CONNECT_TIMEOUT_MESSAGE
    return "network_error", NETWORK_MESSAGE


def tls_context(verify_ssl: bool = True, ca_file: str = "") -> ssl.SSLContext:
    """Teshis adimlarinin kullandigi TLS baglami; ayarlarla ayni davranir."""
    context = ssl.create_default_context(cafile=ca_file or None)
    if not verify_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def split_host_port(value: str, default_port: int = 8080) -> tuple[str, int]:
    """`http://proxy.example.com:8080` ya da `proxy.example.com:8080` ayristirir."""
    raw = (value or "").strip()
    if not raw:
        return "", default_port
    if "://" not in raw:
        raw = "//" + raw
    parsed = urlparse(raw)
    return (parsed.hostname or ""), int(parsed.port or default_port)


def default_port(scheme: str) -> int:
    return 443 if (scheme or "").lower() == "https" else 80
