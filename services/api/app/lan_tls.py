"""Lightweight HTTPS + WSS reverse proxy for phone/LAN access (enables mic & WebSockets on mobile browsers)."""
from __future__ import annotations

import argparse
import asyncio
import datetime
import ipaddress
import re
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_API_PREFIXES = ("/api/", "/healthz", "/docs", "/redoc", "/openapi.json")


def ensure_lan_cert(cert_dir: Path, ips: list[str]) -> tuple[Path, Path]:
    """Create (or refresh if LAN IPs changed) a self-signed SAN certificate for localhost + LAN IPs."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "lan-cert.pem"
    key_path = cert_dir / "lan-key.pem"
    meta_path = cert_dir / "lan-ips.txt"
    wanted = sorted(set(["127.0.0.1", "::1", *ips]))
    if cert_path.exists() and key_path.exists() and meta_path.exists():
        if meta_path.read_text(encoding="utf-8").strip().splitlines() == wanted:
            return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SushrutAI LAN")])
    sans: list[x509.GeneralName] = [x509.DNSName("localhost")]
    for ip_str in wanted:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
        except ValueError:
            sans.append(x509.DNSName(ip_str))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    meta_path.write_text("\n".join(wanted) + "\n", encoding="utf-8")
    return cert_path, key_path


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, ssl.SSLError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _target_port(path: str, web_port: int, api_port: int) -> int:
    return api_port if path.startswith(_API_PREFIXES) else web_port


def _rewrite_headers(header_bytes: bytes, *, force_close: bool) -> tuple[bytes, str, bool]:
    text = header_bytes.decode("latin-1")
    lines = text.split("\r\n")
    req_line = lines[0] if lines else "GET / HTTP/1.1"
    parts = req_line.split(" ", 2)
    path = parts[1] if len(parts) >= 2 else "/"
    is_ws = False
    out_lines = [req_line]
    has_xff = False
    for ln in lines[1:]:
        low = ln.lower()
        if low.startswith("upgrade:") and "websocket" in low:
            is_ws = True
        if low.startswith("x-forwarded-proto:"):
            has_xff = True
        if force_close and low.startswith("connection:"):
            continue
        out_lines.append(ln)
    if not has_xff:
        out_lines.append("X-Forwarded-Proto: https")
    if force_close and not is_ws:
        out_lines.append("Connection: close")
    rebuilt = ("\r\n".join(out_lines) + "\r\n\r\n").encode("latin-1")
    return rebuilt, path, is_ws


async def _handle_client(
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    web_port: int,
    api_port: int,
) -> None:
    up_w: asyncio.StreamWriter | None = None
    try:
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = await asyncio.wait_for(client_r.read(16384), timeout=15)
            if not chunk:
                return
            buf.extend(chunk)
            if len(buf) > 131072:
                return
        sep = buf.index(b"\r\n\r\n")
        head_raw = bytes(buf[:sep])
        rest = bytes(buf[sep + 4:])

        probe = head_raw.decode("latin-1")
        is_upgrade = bool(re.search(r"(?im)^upgrade:\s*websocket\b", probe))
        rebuilt, path, is_ws = _rewrite_headers(head_raw, force_close=not is_upgrade)
        port = _target_port(path, web_port, api_port)

        up_r, up_w = await asyncio.open_connection("127.0.0.1", port)
        up_w.write(rebuilt)
        if rest:
            up_w.write(rest)
        await up_w.drain()

        if is_ws:
            await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))
        else:
            t_in = asyncio.create_task(_pipe(client_r, up_w))
            await _pipe(up_r, client_w)
            t_in.cancel()
    except (ConnectionError, asyncio.TimeoutError, ssl.SSLError, ValueError):
        pass
    finally:
        for w in (up_w, client_w):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass


async def serve_tls(port: int, web_port: int, api_port: int, cert_path: Path, key_path: Path) -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, web_port, api_port),
        host="0.0.0.0",
        port=port,
        ssl=ctx,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3443)
    ap.add_argument("--web-port", type=int, default=3000)
    ap.add_argument("--api-port", type=int, default=8787)
    ap.add_argument("--cert-dir", type=Path, required=True)
    ap.add_argument("--ips", default="")
    args = ap.parse_args()
    ips = [x.strip() for x in args.ips.split(",") if x.strip()]
    cert_path, key_path = ensure_lan_cert(args.cert_dir, ips)
    asyncio.run(serve_tls(args.port, args.web_port, args.api_port, cert_path, key_path))


if __name__ == "__main__":
    main()
