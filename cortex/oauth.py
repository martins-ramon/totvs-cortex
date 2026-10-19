"""Configuração compartilhada dos callbacks Google em produção e localmente."""
import os
from urllib.parse import urlsplit

from flask import request


HTTP_TIMEOUT = 15


def google_credentials():
    """Lê as credenciais no momento da requisição, sem efeitos no import."""
    return os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")


def callback_url(path):
    """Usa a origem pública configurada ou a origem reconhecida pelo Flask.

    APP_BASE_URL deve coincidir com o domínio usado no navegador: o cookie de
    sessão que contém o state OAuth precisa chegar também ao callback.
    """
    origin = os.environ.get("APP_BASE_URL", "").strip()
    if origin:
        parsed = urlsplit(origin)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("APP_BASE_URL deve ser uma origem HTTP(S), sem caminho, credenciais ou parâmetros.")
        # Acesso a .port também rejeita portas inválidas na configuração.
        parsed.port
    else:
        origin = request.url_root
    return origin.rstrip("/") + path
