"""
Coleta partidas ranqueadas (Solo/Duo) de VÁRIOS jogadores via Riot API (match-v5)
e salva o JSON bruto de cada partida e da timeline em data/raw/.

Os jogadores ficam em players.json (veja players.example.json).

Uso:
    PowerShell:  $env:RIOT_API_KEY="RGAPI-xxxxxxxx"
    Mac/Linux:   export RIOT_API_KEY="RGAPI-xxxxxxxx"

    python collect.py                  # todos os jogadores do players.json
    python collect.py --count 30       # padrão de partidas por jogador (se o jogador não tiver "count")
    python collect.py --only eu        # só um jogador (pelo "label")

Observações:
- A chave de desenvolvimento expira a cada 24h (renove em developer.riotgames.com).
- Limites da dev key: 20 req/s e 100 req/2 min. O script espera entre chamadas.
- Partidas já baixadas são puladas, então dá pra rodar várias vezes e interromper (Ctrl+C) à vontade.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

REGION = "americas"  # BR, NA, LAN, LAS usam o roteamento "americas" no match-v5
QUEUE_RANKED_SOLO = 420
PAUSE = 1.3  # segundos entre chamadas (~92 req/2 min, abaixo do limite de 100)

RAW_DIR = Path("data/raw")
MATCH_DIR = RAW_DIR / "matches"
TIMELINE_DIR = RAW_DIR / "timelines"
PLAYERS_OUT = RAW_DIR / "players.json"


def get(url, headers, params=None):
    """GET com tratamento de rate limit (429) e pausa entre chamadas."""
    while True:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 10)) + 1
            print(f"  rate limit atingido, aguardando {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code == 403:
            sys.exit("Erro 403: chave inválida ou expirada. Gere outra em developer.riotgames.com")
        resp.raise_for_status()
        time.sleep(PAUSE)
        return resp.json()


def get_puuid(game_name, tag_line, headers):
    # quote() cuida de espaços e caracteres especiais (acentos, japonês etc.)
    url = (
        f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
        f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
    )
    return get(url, headers)["puuid"]


def get_match_ids(puuid, headers, count):
    """Pagina a lista de IDs (a API retorna no máximo 100 por chamada)."""
    ids, start = [], 0
    while len(ids) < count:
        batch = min(100, count - len(ids))
        url = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
        page = get(url, headers, {"queue": QUEUE_RANKED_SOLO, "start": start, "count": batch})
        if not page:
            break
        ids.extend(page)
        start += len(page)
    return ids


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def upsert_player(entry):
    """Guarda puuid/label/grupo em data/raw/players.json (mantém jogadores de execuções anteriores)."""
    current = json.loads(PLAYERS_OUT.read_text(encoding="utf-8")) if PLAYERS_OUT.exists() else []
    current = [p for p in current if p["label"] != entry["label"]]
    current.append(entry)
    save_json(PLAYERS_OUT, current)


def download_match(match_id, headers):
    match_path = MATCH_DIR / f"{match_id}.json"
    timeline_path = TIMELINE_DIR / f"{match_id}.json"
    if match_path.exists() and timeline_path.exists():
        return False
    base = f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    if not match_path.exists():
        save_json(match_path, get(base, headers))
    if not timeline_path.exists():
        save_json(timeline_path, get(f"{base}/timeline", headers))
    return True


def main():
    parser = argparse.ArgumentParser(description="Coleta partidas ranqueadas da Riot API")
    parser.add_argument("--players", default="players.json", help="Arquivo com a lista de jogadores")
    parser.add_argument("--count", type=int, default=50, help="Partidas por jogador (padrão)")
    parser.add_argument("--only", help="Coletar só o jogador com este label")
    args = parser.parse_args()

    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        sys.exit("Defina a variável de ambiente RIOT_API_KEY")
    headers = {"X-Riot-Token": api_key}

    players_file = Path(args.players)
    if not players_file.exists():
        sys.exit(f"{players_file} não encontrado. Copie players.example.json para players.json e edite.")
    players = json.loads(players_file.read_text(encoding="utf-8"))["players"]
    if args.only:
        players = [p for p in players if p["label"] == args.only]
        if not players:
            sys.exit(f"Nenhum jogador com label '{args.only}' no {players_file}")

    for p in players:
        label = p["label"]
        if "#" not in p["riot_id"]:
            print(f"[{label}] riot_id sem '#': use o formato nome#tag. Pulando.")
            continue
        name, tag = p["riot_id"].rsplit("#", 1)

        try:
            puuid = get_puuid(name, tag, headers)
        except requests.HTTPError as err:
            print(f"[{label}] Riot ID não encontrado (HTTP {err.response.status_code}). Confira nome e tag. Pulando.")
            continue
        upsert_player({"label": label, "group": p.get("group", "referencia"), "puuid": puuid})

        count = p.get("count", args.count)
        match_ids = get_match_ids(puuid, headers, count)
        print(f"[{label}] {len(match_ids)} partidas ranqueadas encontradas (pedi {count}).")

        new = 0
        for i, match_id in enumerate(match_ids, 1):
            try:
                if download_match(match_id, headers):
                    new += 1
                    print(f"  [{label}] {i}/{len(match_ids)} {match_id}")
            except requests.HTTPError as err:
                print(f"  [{label}] {match_id} falhou (HTTP {err.response.status_code}), seguindo.")
        print(f"[{label}] {new} partidas novas baixadas.\n")

    print("Pronto. Dados brutos em data/raw/")


if __name__ == "__main__":
    main()
