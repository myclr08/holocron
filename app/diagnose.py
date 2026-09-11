"""Adim adim ag teshisi: nerede takildigini kullaniciya gosterir.

"Baglanti kurulamadi" tek basina hicbir sey anlatmiyor. Kurum aginda ayni
cumle uc ayri sebepten cikabiliyor: ad cozulmuyor, kapi kapali, vekil sunucu
tuneli acilmiyor. Bu modul zinciri parcalara ayirip her halkayi sureleriyle
raporlar; kullanici kirmizi olan ilk adimi gorur ve yaninda ne yapacagini
okur.

Ciktiya kimlik bilgisi ve token asla girmez. Sunucu adi girer: kullanici zaten
kendi ekranina bakiyor, ayrica takildigi adresi gormeden karar veremez.
"""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from . import net
from .jira_client import JiraError, create_client
from .settings_store import PROXY_DIRECT, PROXY_MANUAL, PROXY_SYSTEM, JiraConfig

OK = "ok"
FAIL = "fail"
SKIP = "skip"

# Her adim kisa tutulur: kullanici ekranin basinda bekliyor.
STEP_TIMEOUT = net.PROBE_TIMEOUT
HTTP_TIMEOUT = (net.PROBE_TIMEOUT, 10.0)

# Cok adresli kayitlarda bütün listeyi denemek dakikalar surebilir.
MAX_ADDRESSES = 4

SERVER_INFO_PATH = "/rest/api/2/serverInfo"


@dataclass
class Step:
    """Tek bir teshis halkasi."""

    key: str
    title: str
    status: str = SKIP
    message: str = ""
    ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "message": self.message,
            "ms": self.ms,
            "detail": self.detail,
        }


class _Timer:
    """Adim suresini milisaniye olarak olcer."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._start = clock()

    @property
    def ms(self) -> int:
        return int(round((self._clock() - self._start) * 1000))


# --- varsayilan sondalar --------------------------------------------------


def _default_connector(family: int, sockaddr: Any, timeout: float) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(sockaddr)
    return sock


def _default_tunneler(sock: Any, host: str, port: int) -> str:
    """Vekil sunucuya CONNECT gonderir; ilk satiri dondurur."""
    request = (
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Proxy-Connection: keep-alive\r\n\r\n"
    ).encode("ascii")
    sock.sendall(request)
    chunks: list[bytes] = []
    while b"\r\n\r\n" not in b"".join(chunks):
        piece = sock.recv(1024)
        if not piece:
            break
        chunks.append(piece)
    head = b"".join(chunks).decode("latin-1", errors="replace")
    return head.splitlines()[0].strip() if head.splitlines() else ""


def _default_tls_prober(sock: Any, host: str, config: JiraConfig) -> dict[str, Any]:
    context = net.tls_context(config.verify_ssl, config.ca_file)
    wrapped = context.wrap_socket(sock, server_hostname=host)
    try:
        cert = wrapped.getpeercert() or {}
        return {
            "version": wrapped.version() or "",
            "subject": _certificate_name(cert.get("subject")),
            "issuer": _certificate_name(cert.get("issuer")),
            "not_after": str(cert.get("notAfter") or ""),
        }
    finally:
        try:
            wrapped.close()
        except OSError:
            pass


def _default_http_prober(
    url: str, proxies: dict[str, str] | None, config: JiraConfig
) -> requests.Response:
    session = net.build_session(
        ipv4_first=config.ipv4_first,
        trust_env=config.proxy_mode != PROXY_DIRECT,
    )
    try:
        return session.get(
            url,
            timeout=HTTP_TIMEOUT,
            proxies=proxies or None,
            verify=_verify_value(config),
            headers={"Accept": "application/json"},
        )
    finally:
        session.close()


def _verify_value(config: JiraConfig) -> bool | str:
    if not config.verify_ssl:
        return False
    return config.ca_file or True


def _certificate_name(value: Any) -> str:
    """`getpeercert()` ic ice demet dondurur; okunur tek satira indirir."""
    if not value:
        return ""
    parts: list[str] = []
    for group in value:
        for pair in group:
            if len(pair) == 2 and pair[0] in ("commonName", "organizationName"):
                parts.append(str(pair[1]))
    return ", ".join(parts)


# --- teshis ---------------------------------------------------------------


def run_diagnostics(
    config: JiraConfig,
    *,
    resolver: Callable[..., list[Any]] | None = None,
    connector: Callable[..., Any] | None = None,
    tunneler: Callable[..., str] | None = None,
    tls_prober: Callable[..., dict[str, Any]] | None = None,
    http_prober: Callable[..., Any] | None = None,
    client_factory: Callable[[JiraConfig], Any] | None = None,
    system_proxies: Callable[[], dict[str, str]] | None = None,
    pac_reader: Callable[[], str] | None = None,
    timeout: float = STEP_TIMEOUT,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Sirayla url → vekil → DNS → TCP → TLS → HTTP → kimlik adimlarini calistirir."""
    def _resolve(host: str, port: int, ipv4_first: bool = True) -> list[Any]:
        return net.resolve(host, port, ipv4_first=ipv4_first)

    resolve = resolver or _resolve
    connect = connector or _default_connector
    tunnel = tunneler or _default_tunneler
    probe_tls = tls_prober or _default_tls_prober
    probe_http = http_prober or _default_http_prober

    steps: list[Step] = []
    advice: list[str] = []

    # 1. Adres ayristirma -------------------------------------------------
    url_step = Step("url", "Adres ayrıştırma")
    steps.append(url_step)
    timer = _Timer(clock)
    parsed = urlparse(config.base_url or "")
    host = parsed.hostname or ""
    scheme = (parsed.scheme or "").lower()
    port = parsed.port or net.default_port(scheme)
    url_step.ms = timer.ms
    if not host or scheme not in ("http", "https"):
        url_step.status = FAIL
        url_step.message = (
            "Jira adresi okunamadı. Adres http:// ya da https:// ile başlamalı, "
            "örneğin https://jira.example.com"
        )
        return _finish(steps, advice, host="")
    url_step.status = OK
    url_step.message = f"{scheme}://{host}:{port}"
    url_step.detail = {"host": host, "port": port, "scheme": scheme}

    # 2. Vekil sunucu kararı ----------------------------------------------
    proxy_step = Step("proxy", "Vekil sunucu")
    steps.append(proxy_step)
    timer = _Timer(clock)
    proxies, source = net.effective_proxies(
        host,
        mode=config.proxy_mode,
        proxy_http=config.proxy_http,
        proxy_https=config.proxy_https,
        no_proxy=config.no_proxy,
        system=system_proxies,
    )
    proxy_url = net.proxy_for(config.base_url, proxies)
    pac = net.pac_url(pac_reader)
    proxy_step.ms = timer.ms
    proxy_step.detail = {
        "source": source,
        "proxy": proxy_url,
        "pac": pac,
        "mode": config.proxy_mode,
    }
    if source == "dogrudan":
        proxy_step.status = OK
        proxy_step.message = (
            "Doğrudan bağlanılacak; vekil sunucu ve ortam değişkenleri yok sayılıyor."
        )
    elif source == "atlandi":
        proxy_step.status = OK
        proxy_step.message = (
            f"Bu adres no_proxy listesinde; vekil sunucu atlanıyor ({config.no_proxy})."
        )
    elif proxy_url:
        label = (
            "ayarlardan"
            if source == "ayar"
            else "sisteminizden (ortam değişkeni ya da kayıt defteri)"
        )
        proxy_step.status = OK
        proxy_step.message = f"Vekil sunucu {label}: {proxy_url}"
    elif pac:
        proxy_step.status = FAIL
        proxy_step.message = (
            f"Sisteminizde PAC (otomatik yapılandırma) tanımlı: {pac}. "
            "Holocron PAC dosyasını çözmez. Jira için geçerli vekil sunucuyu ağ yöneticinizden "
            "öğrenip Ayarlar → Ağ'a yazın; Jira iç ağdaysa \"Doğrudan bağlan\" seçin."
        )
        advice.append(proxy_step.message)
    else:
        proxy_step.status = SKIP
        proxy_step.message = "Vekil sunucu tanımlı değil; doğrudan bağlanılacak."

    if pac and proxy_url:
        proxy_step.message += f" (Sisteminizde ayrıca PAC tanımlı: {pac})"

    # 3. DNS ---------------------------------------------------------------
    dns_step = Step("dns", "Ad çözümleme (DNS)")
    steps.append(dns_step)
    timer = _Timer(clock)
    try:
        infos = resolve(host, port, config.ipv4_first)
    except socket.gaierror as exc:
        dns_step.ms = timer.ms
        dns_step.status = FAIL
        dns_step.message = (
            f"{host} adı çözülemedi: {exc.strerror or exc}. Adresi yeniden yazın; "
            "iç ağ adresiyse VPN'e bağlanmanız ya da kurum DNS sunucusunu kullanmanız gerekebilir."
        )
        return _finish(steps, advice, host=host)
    except OSError as exc:
        dns_step.ms = timer.ms
        dns_step.status = FAIL
        dns_step.message = f"{host} adı çözülemedi: {exc}"
        return _finish(steps, advice, host=host)

    dns_step.ms = timer.ms
    addresses = net.addresses_of(infos)
    families = [net.family_name(info[0]) for info in infos]
    dns_step.detail = {
        "addresses": addresses,
        "families": families,
        "ipv4_first": config.ipv4_first,
    }
    if not infos:
        dns_step.status = FAIL
        dns_step.message = f"{host} için hiç adres dönmedi."
        return _finish(steps, advice, host=host)
    dns_step.status = OK
    dns_step.message = f"{len(addresses)} adres: " + ", ".join(
        f"{addr} ({fam})" for addr, fam in zip(addresses, families)
    )
    if "IPv6" in families and not config.ipv4_first:
        advice.append(
            "Sunucunun IPv6 kaydı var ve \"Önce IPv4 dene\" kapalı. "
            "IPv6 yolu kapalı ağlarda bağlantı otuz saniye askıda kalır; kutuyu açın."
        )

    # 4. TCP: doğrudan -----------------------------------------------------
    direct_step = Step("tcp", "TCP bağlantısı (doğrudan)")
    steps.append(direct_step)
    direct_ok, direct_sock_info = _probe_addresses(
        direct_step, infos[:MAX_ADDRESSES], connect, timeout, clock, host, port
    )

    # 5. TCP + CONNECT: vekil sunucu üzerinden -----------------------------
    proxy_step_tcp = Step("tcp_proxy", "TCP bağlantısı (vekil sunucu üzerinden)")
    steps.append(proxy_step_tcp)
    proxy_ok = False
    proxy_timed_out = False
    if not proxy_url:
        proxy_step_tcp.status = SKIP
        proxy_step_tcp.message = "Vekil sunucu yok, bu adım atlandı."
    else:
        proxy_ok, proxy_timed_out = _probe_proxy(
            proxy_step_tcp, proxy_url, host, port, resolve, connect, tunnel, timeout, clock
        )

    if direct_ok and proxy_url and not proxy_ok:
        advice.append(
            "Doğrudan bağlantı açıldı ama vekil sunucu tüneli açılmadı. "
            "Jira iç ağda olabilir: Ayarlar → Ağ → \"Doğrudan bağlan\" seçin."
        )
    elif proxy_url and not proxy_ok and proxy_timed_out:
        advice.append(
            "Vekil sunucu tüneli zaman aşımına uğradı (CONNECT yanıtsız). "
            "Jira iç ağda olabilir: Ayarlar → Ağ → \"Doğrudan bağlan\" seçin."
        )
    if not direct_ok and not proxy_url and config.proxy_mode != PROXY_DIRECT:
        advice.append(
            "TCP hiç açılmadı ve tanımlı vekil sunucu yok. Ağınız vekil sunucu istiyorsa "
            "adresini Ayarlar → Ağ'a yazın, güvenlik duvarı engelliyorsa ağ yöneticinize sorun."
        )

    if not direct_ok and not proxy_ok:
        return _finish(steps, advice, host=host)

    path = "dogrudan" if direct_ok else "vekil"

    # 6. TLS ---------------------------------------------------------------
    tls_step = Step("tls", "TLS el sıkışması")
    steps.append(tls_step)
    if scheme != "https":
        tls_step.status = SKIP
        tls_step.message = "Adres http:// ile başlıyor, TLS kullanılmıyor."
    else:
        _probe_tls(
            tls_step,
            config,
            host=host,
            port=port,
            path=path,
            address=direct_sock_info,
            proxy_url=proxy_url,
            resolve=resolve,
            connect=connect,
            tunnel=tunnel,
            probe_tls=probe_tls,
            timeout=timeout,
            clock=clock,
            advice=advice,
        )
        if tls_step.status == FAIL:
            return _finish(steps, advice, host=host, path=path)

    # 7. HTTP (kimliksiz) --------------------------------------------------
    http_step = Step("http", "HTTP yanıtı (kimliksiz)")
    steps.append(http_step)
    timer = _Timer(clock)
    try:
        response = probe_http(f"{config.base_url.rstrip('/')}{SERVER_INFO_PATH}", proxies, config)
        http_step.ms = timer.ms
        status = int(getattr(response, "status_code", 0))
        http_step.detail = {"status": status}
        if status in (200, 401, 403):
            http_step.status = OK
            http_step.message = (
                f"Sunucu HTTP {status} döndü; yol açık."
                if status == 200
                else f"Sunucu HTTP {status} döndü: yol açık, kimlik gerekiyor (beklenen)."
            )
        elif status == 404:
            http_step.status = FAIL
            http_step.message = (
                f"HTTP 404: {SERVER_INFO_PATH} bulunamadı. Adresin sonunda bir yol payı "
                "(örneğin /jira) eksik ya da fazla olabilir."
            )
        else:
            http_step.status = FAIL
            http_step.message = (
                f"Sunucu HTTP {status} döndü. Araya bir vekil sunucu ya da oturum açma "
                "sayfası girmiş olabilir."
            )
    except requests.exceptions.RequestException as exc:
        http_step.ms = timer.ms
        http_step.status = FAIL
        code, message = net.classify_connection_error(exc)
        http_step.detail = {"code": code, "via": "vekil sunucu" if proxy_url else "doğrudan"}
        if code == "connect_timeout":
            # Genel mesaj istemcinin 10 sn'sini anlatir; teshis sondasi 5 sn bekler.
            via = (
                f"vekil sunucu ({proxy_url}) üzerinden" if proxy_url else "doğrudan"
            )
            message = (
                f"İstek {via} gönderildi ve {int(net.PROBE_TIMEOUT)} saniyede bağlanamadı."
            )
            if proxy_url:
                message += (
                    " Yukarıdaki doğrudan TCP adımı açıldıysa Jira iç ağdadır: "
                    "Ayarlar → Ağ → \"Doğrudan bağlan\" seçin."
                )
        http_step.message = message

    # 8. Kimlik ------------------------------------------------------------
    auth_step = Step("auth", "Kimlik doğrulama")
    steps.append(auth_step)
    if http_step.status != OK:
        auth_step.status = SKIP
        auth_step.message = "HTTP adımı geçilemedi, kimlik denenmedi."
        return _finish(steps, advice, host=host, path=path)

    timer = _Timer(clock)
    factory = client_factory or (lambda cfg: create_client(cfg))
    try:
        result = factory(config).test_connection()
        auth_step.ms = timer.ms
        auth_step.status = OK
        auth_step.message = "Kimlik doğrulandı: " + (result.get("display_name") or "kullanıcı")
        auth_step.detail = {
            "display_name": result.get("display_name") or "",
            "server_title": result.get("server_title") or "",
            "version": result.get("version") or "",
        }
    except JiraError as exc:
        auth_step.ms = timer.ms
        auth_step.status = FAIL
        auth_step.message = exc.message
        auth_step.detail = {"code": exc.code}
        if exc.code == "auth_failed":
            advice.append(
                "Ağ yolu açık, takılan yalnızca kimlik. Token'ı yeniden üretip "
                "Ayarlar ekranına yapıştırın."
            )

    return _finish(steps, advice, host=host, path=path)


# --- adim yardimcilari ----------------------------------------------------


def _probe_addresses(
    step: Step,
    infos: list[Any],
    connect: Callable[..., Any],
    timeout: float,
    clock: Callable[[], float],
    host: str,
    port: int,
) -> tuple[bool, Any]:
    """Her adrese sirayla baglanir; ilk acilan kazanir."""
    attempts: list[dict[str, Any]] = []
    opened: Any = None
    timer = _Timer(clock)
    for family, _socktype, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0]) if isinstance(sockaddr, tuple) else str(sockaddr)
        single = _Timer(clock)
        try:
            sock = connect(family, sockaddr, timeout)
        except OSError as exc:
            attempts.append(
                {
                    "address": address,
                    "family": net.family_name(family),
                    "status": FAIL,
                    "ms": single.ms,
                    "message": _socket_reason(exc),
                }
            )
            continue
        _close(sock)
        attempts.append(
            {
                "address": address,
                "family": net.family_name(family),
                "status": OK,
                "ms": single.ms,
                "message": "açıldı",
            }
        )
        opened = (family, sockaddr, address)
        break

    step.ms = timer.ms
    step.detail = {"attempts": attempts, "port": port}
    if opened is not None:
        step.status = OK
        step.message = f"{opened[2]}:{port} açıldı ({net.family_name(opened[0])})."
        failed = [item for item in attempts if item["status"] == FAIL]
        if failed:
            step.message += (
                f" Ondan önce {len(failed)} adres başarısız oldu; "
                "\"Önce IPv4 dene\" ayarı sırayı belirliyor."
            )
        return True, opened

    step.status = FAIL
    if attempts:
        reasons = "; ".join(f"{item['address']}: {item['message']}" for item in attempts)
        step.message = f"Hiçbir adrese bağlanılamadı ({reasons}). " + net.CONNECT_TIMEOUT_MESSAGE
    else:
        step.message = f"{host} için denenecek adres yok."
    return False, None


def _probe_proxy(
    step: Step,
    proxy_url: str,
    host: str,
    port: int,
    resolve: Callable[..., list[Any]],
    connect: Callable[..., Any],
    tunnel: Callable[..., str],
    timeout: float,
    clock: Callable[[], float],
) -> tuple[bool, bool]:
    """Vekil sunucuya baglanip CONNECT tuneli acmayi dener."""
    proxy_host, proxy_port = net.split_host_port(proxy_url)
    timer = _Timer(clock)
    step.detail = {"proxy": proxy_url, "target": f"{host}:{port}"}
    if not proxy_host:
        step.ms = timer.ms
        step.status = FAIL
        step.message = f"Vekil sunucu adresi okunamadı: {proxy_url}"
        return False, False

    try:
        infos = resolve(proxy_host, proxy_port, True)
    except OSError as exc:
        step.ms = timer.ms
        step.status = FAIL
        step.message = f"Vekil sunucunun adı çözülemedi ({proxy_host}): {_socket_reason(exc)}"
        return False, False

    sock = None
    for family, _socktype, _proto, _canon, sockaddr in infos[:MAX_ADDRESSES]:
        try:
            sock = connect(family, sockaddr, timeout)
            break
        except OSError as exc:
            step.detail["connect_error"] = _socket_reason(exc)
            sock = None

    if sock is None:
        step.ms = timer.ms
        step.status = FAIL
        step.message = (
            f"Vekil sunucuya ({proxy_host}:{proxy_port}) bağlanılamadı: "
            f"{step.detail.get('connect_error', 'bilinmeyen hata')}"
        )
        return False, False

    try:
        line = tunnel(sock, host, port)
    except (OSError, TimeoutError) as exc:
        step.ms = timer.ms
        step.status = FAIL
        reason = _socket_reason(exc)
        step.message = f"Vekil sunucu CONNECT isteğine yanıt vermedi: {reason}"
        step.detail["tunnel_error"] = reason
        return False, "zaman" in reason.lower() or "timed out" in reason.lower()
    finally:
        _close(sock)

    step.ms = timer.ms
    step.detail["response"] = line
    if " 200" in line:
        step.status = OK
        step.message = f"Vekil sunucu tüneli açıldı: {line}"
        return True, False
    step.status = FAIL
    step.message = (
        f"Vekil sunucu tüneli açılmadı: {line or 'yanıt yok'}. "
        "Jira iç ağdaysa Ayarlar → Ağ → \"Doğrudan bağlan\" seçeneğini deneyin."
    )
    return False, False


def _probe_tls(
    step: Step,
    config: JiraConfig,
    *,
    host: str,
    port: int,
    path: str,
    address: Any,
    proxy_url: str,
    resolve: Callable[..., list[Any]],
    connect: Callable[..., Any],
    tunnel: Callable[..., str],
    probe_tls: Callable[..., dict[str, Any]],
    timeout: float,
    clock: Callable[[], float],
    advice: list[str],
) -> None:
    timer = _Timer(clock)
    sock = None
    try:
        if path == "dogrudan" and address is not None:
            family, sockaddr, _display = address
            sock = connect(family, sockaddr, timeout)
        else:
            proxy_host, proxy_port = net.split_host_port(proxy_url)
            infos = resolve(proxy_host, proxy_port, True)
            family, _st, _pr, _cn, sockaddr = infos[0]
            sock = connect(family, sockaddr, timeout)
            tunnel(sock, host, port)
        info = probe_tls(sock, host, config)
    except ssl.SSLCertVerificationError as exc:
        step.ms = timer.ms
        step.status = FAIL
        step.message = (
            f"Sertifika doğrulanamadı: {exc.verify_message or exc.reason or exc}. "
            "Kurumunuz trafiği kendi kök sertifikasıyla açıyorsa o sertifikayı "
            "Ayarlar → Ağ → Özel CA dosyası alanına verin."
        )
        advice.append(step.message)
        return
    except ssl.SSLError as exc:
        step.ms = timer.ms
        step.status = FAIL
        step.message = f"TLS el sıkışması başarısız: {net.redact_host(str(exc), host)}"
        return
    except OSError as exc:
        step.ms = timer.ms
        step.status = FAIL
        step.message = f"TLS için bağlantı kurulamadı: {_socket_reason(exc)}"
        return
    finally:
        if sock is not None:
            _close(sock)

    step.ms = timer.ms
    step.status = OK
    step.detail = dict(info)
    summary = info.get("version") or "TLS"
    subject = info.get("subject") or ""
    issuer = info.get("issuer") or ""
    parts = [summary]
    if subject:
        parts.append(f"sertifika: {subject}")
    if issuer:
        parts.append(f"veren: {issuer}")
    step.message = " | ".join(parts)
    if not config.verify_ssl:
        step.message += " (doğrulama kapalı)"


def _socket_reason(exc: BaseException) -> str:
    """Soket hatasini kisa Turkce sebebe cevirir."""
    if isinstance(exc, socket.gaierror):
        return "ad çözülemedi"
    if isinstance(exc, ConnectionRefusedError):
        return "reddedildi"
    if isinstance(exc, TimeoutError):
        return "zaman aşımı"
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "zaman aşımı"
    if "refused" in lowered:
        return "reddedildi"
    if "unreachable" in lowered:
        return "ağ erişilemez"
    return message


def _close(sock: Any) -> None:
    try:
        sock.close()
    except Exception:  # noqa: BLE001 - kapatma hatasi teshisi bozmasin
        pass


def _finish(
    steps: list[Step],
    advice: list[str],
    *,
    host: str,
    path: str = "",
) -> dict[str, Any]:
    failed = [step for step in steps if step.status == FAIL]
    ok = not failed
    summary = (
        "Tüm adımlar geçildi."
        if ok
        else f"İlk takılan adım: {failed[0].title}. {failed[0].message}"
    )
    return {
        "ok": ok,
        "host": host,
        "path": path,
        "summary": summary,
        "advice": _dedupe(advice),
        "steps": [step.to_dict() for step in steps],
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = [
    "FAIL",
    "OK",
    "SKIP",
    "PROXY_DIRECT",
    "PROXY_MANUAL",
    "PROXY_SYSTEM",
    "Step",
    "run_diagnostics",
]
