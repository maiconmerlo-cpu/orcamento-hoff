"""Robô de publicação do @maiconmerlo.

Roda no GitHub Actions a cada 15 minutos. Publica pela API oficial do
Instagram (Instagram Login) todo post de posts.json cujo horário já chegou
e que ainda não está em publicados.json.

Segredo necessário no GitHub: IG_TOKEN (token de longa duração do Instagram).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PASTA = Path(__file__).parent
POSTS = PASTA / "posts.json"
PUBLICADOS = PASTA / "publicados.json"
API = "https://graph.instagram.com/v21.0"
JANELA_ATRASO = timedelta(hours=6)  # post mais atrasado que isso é pulado

TOKEN = os.environ.get("IG_TOKEN", "").strip()
SO_TESTE = os.environ.get("SO_TESTE") == "1"


def api(metodo, caminho, **params):
    params["access_token"] = TOKEN
    r = requests.request(metodo, f"{API}/{caminho}", params=params, timeout=60)
    dados = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in dados:
        raise RuntimeError(f"{metodo} {caminho}: {dados.get('error', dados)}")
    return dados


def esperar_pronto(container_id):
    for _ in range(30):
        status = api("GET", container_id, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} com status {status}")
        time.sleep(5)
    raise RuntimeError(f"container {container_id} não ficou pronto a tempo")


def publicar(post):
    imagens = post["imagens"]
    if len(imagens) == 1:
        c = api("POST", "me/media", image_url=imagens[0], caption=post["legenda"])["id"]
    else:
        filhos = []
        for url in imagens[:10]:
            filho = api("POST", "me/media", image_url=url, is_carousel_item="true")["id"]
            esperar_pronto(filho)
            filhos.append(filho)
        c = api(
            "POST", "me/media",
            media_type="CAROUSEL", children=",".join(filhos), caption=post["legenda"],
        )["id"]
    esperar_pronto(c)
    return api("POST", "me/media_publish", creation_id=c)["id"]


def main():
    if not TOKEN:
        print("IG_TOKEN não configurado. Nada a fazer.")
        return 0

    conta = api("GET", "me", fields="username")
    print(f"Conectado como @{conta.get('username')}")
    if SO_TESTE:
        print("Teste de conexão OK.")
        return 0

    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    publicados = json.loads(PUBLICADOS.read_text(encoding="utf-8")) if PUBLICADOS.exists() else {}
    agora = datetime.now(timezone.utc)
    erros = 0

    for post in sorted(posts, key=lambda p: p["quando"]):
        if post["id"] in publicados:
            continue
        quando = datetime.fromisoformat(post["quando"])
        if quando > agora:
            continue
        if agora - quando > JANELA_ATRASO:
            print(f"PULADO (atrasado demais): {post['id']}")
            publicados[post["id"]] = {"status": "pulado", "em": agora.isoformat()}
            continue
        try:
            media_id = publicar(post)
            print(f"PUBLICADO: {post['id']} -> {media_id}")
            publicados[post["id"]] = {"status": "publicado", "media_id": media_id, "em": agora.isoformat()}
        except Exception as e:  # segue para o próximo e tenta de novo na próxima rodada
            erros += 1
            print(f"ERRO em {post['id']}: {e}")

    PUBLICADOS.write_text(json.dumps(publicados, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
