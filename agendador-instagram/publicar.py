"""Robô de publicação do @maiconmerlo.

Roda no GitHub Actions a cada 15 minutos. Publica pela API oficial do
Instagram (login unificado / usuário do sistema) todo post de posts.json cujo horário já chegou
e que ainda não está em publicados.json.

Segredo necessário no GitHub: IG_TOKEN (token de usuário do sistema do app "Agendador maiconmerlo", não expira).
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PASTA = Path(__file__).parent
POSTS = PASTA / "posts.json"
PUBLICADOS = PASTA / "publicados.json"
API = "https://graph.facebook.com/v21.0"
IG_USER_ID = os.environ.get("IG_USER_ID", "17841404701493839")  # @maiconmerlo
JANELA_ATRASO = timedelta(hours=6)  # post mais atrasado que isso é pulado
PLANTAO_MAX = timedelta(hours=5, minutes=30)  # quanto tempo uma execução espera o próximo post

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
        c = api("POST", f"{IG_USER_ID}/media", image_url=imagens[0], caption=post["legenda"])["id"]
    else:
        filhos = []
        for url in imagens[:10]:
            filho = api("POST", f"{IG_USER_ID}/media", image_url=url, is_carousel_item="true")["id"]
            esperar_pronto(filho)
            filhos.append(filho)
        c = api(
            "POST", f"{IG_USER_ID}/media",
            media_type="CAROUSEL", children=",".join(filhos), caption=post["legenda"],
        )["id"]
    esperar_pronto(c)
    return api("POST", f"{IG_USER_ID}/media_publish", creation_id=c)["id"]


def main():
    if not TOKEN:
        print("IG_TOKEN não configurado. Nada a fazer.")
        return 0

    conta = api("GET", IG_USER_ID, fields="username")
    print(f"Conectado como @{conta.get('username')}")
    if SO_TESTE:
        print("Teste de conexão OK.")
        return 0

    posts = json.loads(POSTS.read_text(encoding="utf-8"))
    publicados = json.loads(PUBLICADOS.read_text(encoding="utf-8")) if PUBLICADOS.exists() else {}
    inicio = datetime.now(timezone.utc)
    erros = 0

    # O agendamento do GitHub é irregular (às vezes passa horas sem rodar).
    # Por isso cada execução fica "de plantão": publica o que venceu e, se o
    # próximo post estiver dentro do limite, espera até o horário e publica.
    while True:
        erros += rodada(posts, publicados)
        pendentes = sorted(
            datetime.fromisoformat(p["quando"]) for p in posts if p["id"] not in publicados
        )
        futuros = [q for q in pendentes if q > datetime.now(timezone.utc)]
        if not futuros:
            print("Nenhum post futuro pendente.")
            break
        proximo = futuros[0]
        if proximo > inicio + PLANTAO_MAX:
            resto = (inicio + PLANTAO_MAX - datetime.now(timezone.utc)).total_seconds()
            if resto > 0:
                print(f"Próximo post em {proximo.isoformat()}. Plantão: aguardando {int(resto // 60)} min.", flush=True)
                time.sleep(resto)
            print("Fim do plantão desta execução — passando para a próxima.")
            Path("/tmp/agendador_continuar").write_text(proximo.isoformat())
            break
        espera = (proximo - datetime.now(timezone.utc)).total_seconds() + 20
        print(f"Aguardando {int(espera // 60)} min até {proximo.isoformat()}...", flush=True)
        time.sleep(max(espera, 0))
    return 1 if erros else 0


def salvar(publicados, msg):
    PUBLICADOS.write_text(json.dumps(publicados, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    cmds = [
        ["git", "add", str(PUBLICADOS)],
        ["git", "commit", "-q", "-m", msg],
        ["git", "pull", "-q", "--rebase", "origin", "main"],
        ["git", "push", "-q", "origin", "HEAD:main"],
    ]
    for c in cmds:
        subprocess.run(c, check=False)


def rodada(posts, publicados):
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
            salvar(publicados, f"Agendador: pulado {post['id']}")
            continue
        try:
            media_id = publicar(post)
            print(f"PUBLICADO: {post['id']} -> {media_id}", flush=True)
            publicados[post["id"]] = {"status": "publicado", "media_id": media_id,
                                      "em": datetime.now(timezone.utc).isoformat()}
            salvar(publicados, f"Agendador: publicado {post['id']}")
        except Exception as e:  # tenta de novo na próxima rodada
            erros += 1
            print(f"ERRO em {post['id']}: {e}", flush=True)
    return erros


if __name__ == "__main__":
    sys.exit(main())
