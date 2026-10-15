"""
Mergulho na fase de rota (0-15 min): extrai da timeline o que explica a
diferença de ouro entre o jogador e o adversário direto da mesma posição.

Gera data/deep_15.csv com uma linha por partida de cada jogador monitorado,
na role escolhida (padrão BOTTOM = ADC):

  - gd_1..gd_15, xd_1..xd_15, cd_1..cd_15, ld_1..ld_15
        diferença MINUTO A MINUTO de ouro, XP, CS e nível contra o adversário direto
        (mostra QUANDO a diferença se abre)
  - kills/deaths/assists/killgold de cada um aos minutos 5, 10 e 15
  - plates_me/plates_opp: placas de torre destruídas pelo jogador / pelo adversário
  - botplates_team/botplates_enemy: placas da rota inferior derrubadas pelo time / contra o time
  - dragons, grubs (HORDE) e torres do time e do inimigo
  - wards_me / wardkills_me: sentinelas colocadas e destruídas
  - first_back_s: segundo da primeira compra após o início (aprox. primeiro recall)
  - first_kill_s / first_death_s
  - opp_is_tracked: 1 se o adversário direto também é um jogador da lista
    (partida espelhada, deve ser tratada à parte)

Uso:
    python deep_dive.py
    python deep_dive.py --role JUNGLE
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path("data/raw")
OUT = Path("data/deep_15.csv")
MINUTES = range(1, 16)
CHECKPOINTS = (5, 10, 15)
MIN_DURATION_S = 300
START_SHOP_MS = 120_000  # compras antes de 2:00 são o kit inicial, não o primeiro recall

COUNTERS = [
    "kills_me", "deaths_me", "assists_me",
    "kills_opp", "deaths_opp", "assists_opp",
    "killgold_me", "killgold_opp",
    "plates_me", "plates_opp",
    "botplates_team", "botplates_enemy",
    "dragons_team", "dragons_enemy", "grubs_team", "grubs_enemy",
    "towers_team", "towers_enemy",
    "wards_me", "wardkills_me",
]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cs_of(pf):
    return pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0)


def analyze(match, timeline, player, tracked, role):
    info = match["info"]
    me = next((p for p in info["participants"] if p["puuid"] == player["puuid"]), None)
    if me is None or me.get("teamPosition") != role:
        return None
    opp = next(
        (p for p in info["participants"]
         if p["teamId"] != me["teamId"] and p.get("teamPosition") == role),
        None,
    )
    if opp is None:
        return None

    pid, opp_pid, team_id = me["participantId"], opp["participantId"], me["teamId"]
    frames = timeline["info"]["frames"]

    row = {
        "player": player["label"],
        "group": player["group"],
        "match_id": match["metadata"]["matchId"],
        "patch": ".".join(info["gameVersion"].split(".")[:2]),
        "patch_minor": int(info["gameVersion"].split(".")[1]),
        "duration_min": round(info["gameDuration"] / 60, 1),
        "champion": me["championName"],
        "opp_champion": opp["championName"],
        "win": int(me["win"]),
        "opp_is_tracked": int(opp["puuid"] in tracked),
    }

    # --- curva minuto a minuto ---
    for m in MINUTES:
        if len(frames) > m:
            pf = frames[m]["participantFrames"]
            a, b = pf[str(pid)], pf[str(opp_pid)]
            row[f"gd_{m}"] = a["totalGold"] - b["totalGold"]
            row[f"xd_{m}"] = a["xp"] - b["xp"]
            row[f"cd_{m}"] = cs_of(a) - cs_of(b)
            row[f"ld_{m}"] = a["level"] - b["level"]
        else:
            for k in ("gd", "xd", "cd", "ld"):
                row[f"{k}_{m}"] = np.nan

    # --- eventos até o minuto 15 ---
    counts = {f"{name}_{m}": 0 for name in COUNTERS for m in CHECKPOINTS}

    def bump(name, ts, amount=1):
        for m in CHECKPOINTS:
            if ts <= m * 60_000:
                counts[f"{name}_{m}"] += amount

    first_kill = first_death = first_back = None
    for frame in frames[:16]:
        for ev in frame.get("events", []):
            t, ts = ev.get("type"), ev.get("timestamp", 0)

            if t == "CHAMPION_KILL":
                killer, victim = ev.get("killerId"), ev.get("victimId")
                assists = ev.get("assistingParticipantIds", [])
                gold = ev.get("bounty", 0) + ev.get("shutdownBounty", 0)
                if killer == pid:
                    bump("kills_me", ts)
                    bump("killgold_me", ts, gold)
                    first_kill = ts if first_kill is None else min(first_kill, ts)
                if victim == pid:
                    bump("deaths_me", ts)
                    first_death = ts if first_death is None else min(first_death, ts)
                if pid in assists:
                    bump("assists_me", ts)
                if killer == opp_pid:
                    bump("kills_opp", ts)
                    bump("killgold_opp", ts, gold)
                if victim == opp_pid:
                    bump("deaths_opp", ts)
                if opp_pid in assists:
                    bump("assists_opp", ts)

            elif t == "TURRET_PLATE_DESTROYED":
                bot = ev.get("laneType") == "BOT_LANE"
                if ev.get("teamId") != team_id:  # placa do inimigo, derrubada pelo meu time
                    if bot:
                        bump("botplates_team", ts)
                else:  # placa do meu time, derrubada pelo inimigo
                    if bot:
                        bump("botplates_enemy", ts)
                if ev.get("killerId") == pid:
                    bump("plates_me", ts)
                if ev.get("killerId") == opp_pid:
                    bump("plates_opp", ts)

            elif t == "ELITE_MONSTER_KILL":
                mine = ev.get("killerTeamId") == team_id
                kind = ev.get("monsterType")
                if kind == "DRAGON":
                    bump("dragons_team" if mine else "dragons_enemy", ts)
                elif kind == "HORDE":
                    bump("grubs_team" if mine else "grubs_enemy", ts)

            elif t == "BUILDING_KILL" and ev.get("buildingType") == "TOWER_BUILDING":
                bump("towers_team" if ev.get("teamId") != team_id else "towers_enemy", ts)

            elif t == "WARD_PLACED" and ev.get("creatorId") == pid:
                bump("wards_me", ts)
            elif t == "WARD_KILL" and ev.get("killerId") == pid:
                bump("wardkills_me", ts)

            elif t == "ITEM_PURCHASED" and ev.get("participantId") == pid and ts >= START_SHOP_MS:
                first_back = ts if first_back is None else min(first_back, ts)

    for m in CHECKPOINTS:  # partida mais curta que o checkpoint: sem dado
        if len(frames) <= m:
            for name in COUNTERS:
                counts[f"{name}_{m}"] = np.nan
    row.update(counts)

    row["first_kill_s"] = first_kill / 1000 if first_kill is not None else np.nan
    row["first_death_s"] = first_death / 1000 if first_death is not None else np.nan
    row["first_back_s"] = first_back / 1000 if first_back is not None else np.nan
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="BOTTOM", help="BOTTOM (ADC), JUNGLE, MIDDLE, TOP, UTILITY")
    args = parser.parse_args()
    role = args.role.upper()

    players_file = RAW / "players.json"
    if not players_file.exists():
        raise SystemExit("data/raw/players.json não encontrado. Rode collect.py primeiro.")
    players = {p["puuid"]: p for p in load(players_file)}

    rows, skipped = [], 0
    for match_path in sorted((RAW / "matches").glob("*.json")):
        timeline_path = RAW / "timelines" / match_path.name
        if not timeline_path.exists():
            skipped += 1
            continue
        try:
            match = load(match_path)
            if match["info"]["gameDuration"] < MIN_DURATION_S:
                continue
            mine = [players[u] for u in match["metadata"]["participants"] if u in players]
            if not mine:
                continue
            timeline = load(timeline_path)
            for player in mine:
                row = analyze(match, timeline, player, players, role)
                if row:
                    rows.append(row)
        except (KeyError, StopIteration, TypeError) as err:
            print(f"  pulando {match_path.name}: problema em {err!r}")
            skipped += 1

    if not rows:
        raise SystemExit(f"Nenhuma partida na role {role}.")

    df = pd.DataFrame(rows).sort_values(["player", "match_id"]).reset_index(drop=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")

    print(f"{len(df)} linhas na role {role} ({skipped} partidas puladas). Salvo em {OUT}\n")
    print(df.groupby("player").agg(
        partidas=("win", "count"),
        espelhadas=("opp_is_tracked", "sum"),
        primeiro_back_s=("first_back_s", "median"),
        placas_15=("plates_me_15", "mean"),
    ).round(1).to_string())
    print("\nManda o arquivo data/deep_15.csv para o Claude analisar.")


if __name__ == "__main__":
    main()
