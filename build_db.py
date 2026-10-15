"""
Transforma o JSON bruto (data/raw/) numa tabela com uma linha por partida
POR JOGADOR monitorado (se dois jogadores da lista caírem na mesma partida,
saem duas linhas).

Gera:
    data/lol.db              banco SQLite (tabela "matches")
    data/matches.csv         mesma tabela em CSV (abre direto no Excel)
    data/matches_<ROLE>.csv  (só com --role) apenas a role escolhida

Uso:
    python build_db.py
    python build_db.py --role BOTTOM      # BOTTOM = ADC | JUNGLE | MIDDLE | TOP | UTILITY

Métricas de timeline (aos 10 e 15 min) são do campeão do jogador; a diferença
é calculada contra o adversário da mesma posição (ex.: ADC vs ADC).
"""

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

RAW = Path("data/raw")
DB_PATH = Path("data/lol.db")
CSV_PATH = Path("data/matches.csv")
CHECKPOINTS = (10, 15)  # minutos
MIN_DURATION_S = 300  # abaixo disso é remake/dodge


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cs_of(pf):
    return pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0)


def kda_until(frames, minute, pid):
    """Conta abates, mortes e assistências do participante até o minuto indicado."""
    kills = deaths = assists = 0
    for frame in frames[: minute + 1]:
        for ev in frame.get("events", []):
            if ev.get("type") != "CHAMPION_KILL":
                continue
            if ev.get("killerId") == pid:
                kills += 1
            if ev.get("victimId") == pid:
                deaths += 1
            if pid in ev.get("assistingParticipantIds", []):
                assists += 1
    return kills, deaths, assists


def objective(team, name, key):
    return team.get("objectives", {}).get(name, {}).get(key)


def build_row(match, timeline, player):
    info = match["info"]
    me = next((p for p in info["participants"] if p["puuid"] == player["puuid"]), None)
    if me is None:
        return None

    pid, team_id, pos = me["participantId"], me["teamId"], me.get("teamPosition", "")
    opp = None
    if pos:
        opp = next(
            (p for p in info["participants"] if p["teamId"] != team_id and p.get("teamPosition") == pos),
            None,
        )
    my_team = next((t for t in info["teams"] if t["teamId"] == team_id), {})
    ally_ids = [p["participantId"] for p in info["participants"] if p["teamId"] == team_id]
    enemy_ids = [p["participantId"] for p in info["participants"] if p["teamId"] != team_id]
    team_kills = sum(p["kills"] for p in info["participants"] if p["teamId"] == team_id)

    created = pd.to_datetime(info["gameCreation"], unit="ms", utc=True).tz_convert("America/Sao_Paulo")
    duration_min = info["gameDuration"] / 60
    cs_total = me["totalMinionsKilled"] + me.get("neutralMinionsKilled", 0)

    row = {
        "player": player["label"],
        "group": player["group"],
        "match_id": match["metadata"]["matchId"],
        "date": created.strftime("%Y-%m-%d %H:%M"),
        "hour": created.hour,
        "weekday": created.day_name(),
        "patch": ".".join(info["gameVersion"].split(".")[:2]),
        "patch_minor": int(info["gameVersion"].split(".")[1]),  # numérico: o Excel transforma 16.20 em 16,2
        "duration_min": round(duration_min, 1),
        "champion": me["championName"],
        "position": pos,
        "opp_champion": opp["championName"] if opp else None,
        "win": int(me["win"]),
        "kills": me["kills"],
        "deaths": me["deaths"],
        "assists": me["assists"],
        "kill_participation": round((me["kills"] + me["assists"]) / team_kills, 3) if team_kills else None,
        "cs_total": cs_total,
        "cs_per_min": round(cs_total / duration_min, 2),
        "gold": me["goldEarned"],
        "gold_per_min": round(me["goldEarned"] / duration_min, 1),
        "damage_to_champs": me["totalDamageDealtToChampions"],
        "damage_per_min": round(me["totalDamageDealtToChampions"] / duration_min, 1),
        "vision_score": me["visionScore"],
        "first_blood": int(me.get("firstBloodKill", False) or me.get("firstBloodAssist", False)),
        "team_first_blood": int(bool(objective(my_team, "champion", "first"))),
        "team_first_dragon": int(bool(objective(my_team, "dragon", "first"))),
        "team_first_tower": int(bool(objective(my_team, "tower", "first"))),
        "team_dragons": objective(my_team, "dragon", "kills"),
        "team_barons": objective(my_team, "baron", "kills"),
        "team_towers": objective(my_team, "tower", "kills"),
    }

    frames = timeline["info"]["frames"]
    for m in CHECKPOINTS:
        keys = ["gold", "xp", "level", "cs", "gold_diff", "xp_diff", "cs_diff",
                "team_gold_diff", "kills", "deaths", "assists"]
        if len(frames) <= m:  # partida terminou antes desse minuto
            for k in keys:
                row[f"{k}_{m}"] = None
            continue

        pfs = frames[m]["participantFrames"]
        mine = pfs[str(pid)]
        row[f"gold_{m}"] = mine["totalGold"]
        row[f"xp_{m}"] = mine["xp"]
        row[f"level_{m}"] = mine["level"]
        row[f"cs_{m}"] = cs_of(mine)

        if opp:
            theirs = pfs[str(opp["participantId"])]
            row[f"gold_diff_{m}"] = mine["totalGold"] - theirs["totalGold"]
            row[f"xp_diff_{m}"] = mine["xp"] - theirs["xp"]
            row[f"cs_diff_{m}"] = cs_of(mine) - cs_of(theirs)
        else:
            row[f"gold_diff_{m}"] = row[f"xp_diff_{m}"] = row[f"cs_diff_{m}"] = None

        row[f"team_gold_diff_{m}"] = (
            sum(pfs[str(i)]["totalGold"] for i in ally_ids)
            - sum(pfs[str(i)]["totalGold"] for i in enemy_ids)
        )
        k, d, a = kda_until(frames, m, pid)
        row[f"kills_{m}"], row[f"deaths_{m}"], row[f"assists_{m}"] = k, d, a

    return row


def load_players():
    players_file = RAW / "players.json"
    if players_file.exists():
        return load(players_file)
    old = RAW / "player.json"  # formato da primeira versão (um jogador só)
    if old.exists():
        return [{"label": "eu", "group": "eu", "puuid": load(old)["puuid"]}]
    raise SystemExit("Nenhum jogador em data/raw/. Rode collect.py primeiro.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", help="Também salva um CSV só com esta posição (ex.: BOTTOM)")
    args = parser.parse_args()

    players = {p["puuid"]: p for p in load_players()}

    rows, skipped, remakes = [], 0, 0
    for match_path in sorted((RAW / "matches").glob("*.json")):
        timeline_path = RAW / "timelines" / match_path.name
        if not timeline_path.exists():
            skipped += 1
            continue
        try:
            match = load(match_path)
            if match["info"]["gameDuration"] < MIN_DURATION_S:  # remake / dodge: não é partida real
                remakes += 1
                continue
            tracked = [players[u] for u in match["metadata"]["participants"] if u in players]
            if not tracked:
                continue
            timeline = load(timeline_path)
            for player in tracked:
                row = build_row(match, timeline, player)
                if row:
                    rows.append(row)
        except (KeyError, StopIteration) as err:
            print(f"  pulando {match_path.name}: campo ausente {err}")
            skipped += 1

    if not rows:
        raise SystemExit("Nenhuma partida processada.")

    df = pd.DataFrame(rows).sort_values(["player", "date"]).reset_index(drop=True)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        df.to_sql("matches", conn, if_exists="replace", index=False)
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")  # utf-8-sig: Excel lê acentos certo

    print(f"{len(df)} linhas (jogador x partida); {remakes} remakes descartados, {skipped} puladas.")
    summary = (
        df.groupby(["player", "position"])["win"]
        .agg(partidas="count", winrate="mean")
        .round(3)
    )
    print("\nPor jogador e posição:")
    print(summary.to_string())

    if args.role:
        role_df = df[df["position"] == args.role.upper()]
        role_path = Path(f"data/matches_{args.role.upper()}.csv")
        role_df.to_csv(role_path, index=False, encoding="utf-8-sig")
        print(f"\n{len(role_df)} linhas na posição {args.role.upper()} salvas em {role_path}")

    print(f"\nSalvo em {DB_PATH} e {CSV_PATH}")


if __name__ == "__main__":
    main()
