"""
Análise final do projeto: benchmark de ADC (eu vs. jogadores de referência).

Lê os CSVs gerados pelo pipeline e produz:
    figures/fig1_gold_curve.png      diferença de ouro minuto a minuto
    figures/fig2_decomposition.png   de onde vem a diferença de ouro aos 15 min
    figures/fig3_early_game.png      como as primeiras trocas diferem
    results.json                     todos os números citados no README

Entradas (geradas por build_db.py e deep_dive.py):
    data/matches_BOTTOM.csv
    data/deep_15.csv

Uso:
    python analysis.py
    python analysis.py --data-dir data --out-dir figures

Decisões de análise (documentadas no README):
  - Partidas "espelhadas" (dois jogadores de referência na mesma rota, um contra o
    outro) são removidas: as linhas são o espelho uma da outra e distorcem a média.
  - Todas as incertezas são intervalos de confiança de 95% por bootstrap.
  - A decomposição do ouro é uma regressão linear (diferença de ouro aos 15 min ~
    CS, ouro de abates, assistências, placas) seguida de uma decomposição tipo
    Oaxaca-Blinder: contribuição = coeficiente x diferença média entre os grupos.
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SEED = 7
N_BOOT = 3000
MINUTES = list(range(1, 16))

# Paleta de referência (validada para daltonismo): série 1 = azul, série 2 = laranja
SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
C_EU, C_REF = "#2a78d6", "#eb6834"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False,
})


def pt(v, dec=0, sign=False):
    """Formata número no padrão brasileiro (1.234,5)."""
    s = f"{abs(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if v < 0:
        return "−" + s
    return ("+" + s) if sign else s


# ---------------------------------------------------------------- dados
def load(data_dir):
    deep = pd.read_csv(data_dir / "deep_15.csv")
    matches = pd.read_csv(data_dir / "matches_BOTTOM.csv", dtype={"patch": str})
    extra = matches[["player", "match_id", "cs_per_min", "cs_15", "damage_per_min",
                     "vision_score", "kill_participation", "gold_per_min"]]
    deep = deep.merge(extra, on=["player", "match_id"], how="left").copy()  # copy: evita fragmentação
    deep["kg_diff"] = deep.killgold_me_15 - deep.killgold_opp_15
    deep["a_diff"] = deep.assists_me_15 - deep.assists_opp_15
    deep["pl_diff"] = deep.plates_me_15 - deep.plates_opp_15
    deep["tpl_diff"] = deep.botplates_team_15 - deep.botplates_enemy_15
    deep["kill_by_5"] = (deep.kills_me_5 >= 1) * 100.0
    deep["death_by_5"] = (deep.deaths_me_5 >= 1) * 100.0
    deep["kd_net_15"] = deep.kills_me_15 - deep.deaths_me_15
    deep["kp_pct"] = deep.kill_participation * 100
    return deep


# ---------------------------------------------------------------- estatística
def boot_means(arr, rng):
    """Médias bootstrap por coluna (ignora NaN). arr: (n, k) -> (N_BOOT, k)."""
    n = arr.shape[0]
    idx = rng.integers(0, n, size=(N_BOOT, n))
    return np.nanmean(arr[idx], axis=1)


def boot_diff(a, b, rng):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    diffs = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(N_BOOT)]
    return np.percentile(diffs, [2.5, 97.5])


def auc(x, y):
    s = pd.DataFrame({"x": x, "y": y}).dropna()
    r = s.x.rank()
    n1, n0 = (s.y == 1).sum(), (s.y == 0).sum()
    return float((r[s.y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


FEATS = ["cd_15", "kg_diff", "a_diff", "pl_diff", "tpl_diff"]
FEAT_LABELS = {
    "cd_15": "CS à frente do adversário",
    "kg_diff": "Ouro dos abates",
    "pl_diff": "Placas de torre (você vs. adversário)",
    "a_diff": "Assistências",
    "tpl_diff": "Placas do time na rota inferior",
}


def decompose(e, r):
    d = pd.concat([e, r])
    X = np.column_stack([np.ones(len(d))] + [d[f] for f in FEATS])
    y = d.gd_15.values
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    r2 = 1 - ((y - X @ beta) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    contrib = {f: beta[k] * (r[f].mean() - e[f].mean()) for k, f in enumerate(FEATS, 1)}
    gap = r.gd_15.mean() - e.gd_15.mean()
    return beta, r2, contrib, gap - sum(contrib.values()), gap


# ---------------------------------------------------------------- gráficos
def base_fig(figsize, title, subtitle, note):
    fig = plt.figure(figsize=figsize, dpi=160, facecolor=SURFACE)
    fig.text(0.055, 0.955, title, fontsize=15, fontweight="bold", color=INK, va="top")
    fig.text(0.055, 0.905, subtitle, fontsize=10.5, color=INK2, va="top")
    fig.text(0.055, 0.025, note, fontsize=8.5, color=MUTED, va="bottom")
    return fig


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=9.5)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(AXIS)


def fig_curve(res, out, n_eu, n_ref):
    c = res["curve"]
    x = np.array(MINUTES)
    fig = base_fig(
        (10, 5.8),
        f"A diferença de ouro se abre aos poucos e fica clara a partir do minuto {res['first_clear_minute']}",
        "Diferença média de ouro contra o ADC inimigo, minuto a minuto (faixa = intervalo de confiança de 95%)",
        f"{n_eu} partidas suas; {n_ref} partidas de 5 jogadores de referência (partidas espelhadas removidas).",
    )
    ax = fig.add_axes([0.075, 0.115, 0.86, 0.69])
    style_axes(ax)
    for key, color, name in (("eu", C_EU, "Você"), ("ref", C_REF, "Referência")):
        m = np.array(c[f"{key}_mean"]); lo = np.array(c[f"{key}_lo"]); hi = np.array(c[f"{key}_hi"])
        ax.fill_between(x, lo, hi, color=color, alpha=0.13, lw=0)
        ax.plot(x, m, color=color, lw=2, marker="o", ms=4, mec=SURFACE, mew=1.2, label=name, zorder=3)
        ax.text(15.35, m[-1], f"{name}\n{pt(m[-1], 0, True)}", color=INK, fontsize=10, fontweight="bold",
                va="center", ha="left")
    ax.axhline(0, color=AXIS, lw=1.2, zorder=2)
    fc = res["first_clear_minute"]
    ax.axvline(fc, color=MUTED, lw=0.9, zorder=1)
    ax.text(fc + 0.2, 1650, "a partir daqui o intervalo de confiança\nda diferença (referência − você) exclui o zero",
            fontsize=9, color=INK2, va="top")
    ax.set_xlim(0.6, 17.6)
    ax.set_ylim(-900, 1700)
    ax.set_xticks(MINUTES)
    ax.set_xlabel("Minuto de jogo", fontsize=10)
    ax.set_ylabel("Ouro a mais que o ADC inimigo", fontsize=10)
    ax.yaxis.set_major_formatter(lambda v, _: pt(v))
    ax.legend(loc="upper left", frameon=False, fontsize=10, labelcolor=INK)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def fig_decomp(res, out):
    d = res["decomposition"]
    items = sorted(d["parts"], key=lambda p: (p["key"] == "rest", -p["value"]))
    gap = d["gap_total"]
    cs_kill = sum(p["value"] for p in d["parts"] if p["key"] in ("cd_15", "kg_diff"))
    fig = base_fig(
        (10, 5.4),
        f"CS e abates explicam cerca de {round(100 * cs_kill / gap)}% da diferença de ouro aos 15 minutos",
        f"De onde vêm os {pt(gap)} de ouro a mais dos jogadores de referência (barra de erro = IC 95%)",
        f"Regressão linear (R² = {pt(d['r2'], 2)}) + decomposição das médias; n = {res['n']['eu']} + {res['n']['ref']} partidas.",
    )
    ax = fig.add_axes([0.34, 0.13, 0.62, 0.67])
    style_axes(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, lw=0.8)
    y = np.arange(len(items))[::-1]
    for yi, p in zip(y, items):
        color = AXIS if p["key"] == "rest" else C_EU
        ax.barh(yi, p["value"], height=0.5, color=color, zorder=3)
        ax.errorbar(p["value"], yi, xerr=[[p["value"] - p["lo"]], [p["hi"] - p["value"]]],
                    color=INK2, lw=1.2, capsize=3, zorder=4)
        ax.text(max(p["hi"], p["value"]) + 25, yi, f"{pt(p['value'], 0, True)}  ({round(100 * p['value'] / gap)}%)",
                va="center", fontsize=10, color=INK)
    ax.axvline(0, color=AXIS, lw=1.2, zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels([p["label"] for p in items], fontsize=10.5, color=INK)
    ax.set_xlim(-150, 1250)
    ax.xaxis.set_major_formatter(lambda v, _: pt(v))
    ax.set_xlabel("Ouro explicado aos 15 minutos", fontsize=10)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def fig_early(res, out):
    e = res["early"]
    panels = [("kill_by_5", "Partidas com abate\naté o minuto 5 (%)", 0),
              ("death_by_5", "Partidas com morte\naté o minuto 5 (%)", 0),
              ("cd_5", "CS à frente do adversário\nno minuto 5", 1),
              ("wards_me_15", "Sentinelas colocadas\naté o minuto 15", 1)]
    fig = base_fig(
        (10, 6.0),
        "Eles trocam mais cedo, ficam na frente no CS e usam o dobro de sentinelas",
        "Primeiros minutos da rota, você vs. jogadores de referência",
        f"{res['n']['eu']} partidas suas; {res['n']['ref']} de referência. Médias; diferenças com IC 95% no README.",
    )
    axes = fig.subplots(1, 4, gridspec_kw={"left": 0.05, "right": 0.97, "top": 0.74, "bottom": 0.16, "wspace": 0.35})
    for ax, (key, title, dec) in zip(axes, panels):
        style_axes(ax)
        ax.grid(axis="y", visible=False)
        vals = [e[key]["eu"], e[key]["ref"]]
        ax.bar([0, 1], vals, width=0.62, color=[C_EU, C_REF], zorder=3)
        lo, hi = min(0, min(vals)), max(0, max(vals))
        pad = (hi - lo) * 0.18
        ax.set_ylim(lo - (pad if lo < 0 else 0), hi + pad)
        ax.axhline(0, color=AXIS, lw=1.2, zorder=2)
        for xi, v in zip([0, 1], vals):
            ax.text(xi, v + (pad * 0.25 if v >= 0 else -pad * 0.25), pt(v, dec, key == "cd_5"), ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=11, fontweight="bold", color=INK)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Você", "Referência"], fontsize=9.5, color=INK2)
        ax.set_yticks([])
        ax.spines["left"].set_visible(False)
        ax.set_title(title, fontsize=10, color=INK, loc="center", pad=10)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


# ---------------------------------------------------------------- principal
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out-dir", default="figures")
    args = parser.parse_args()
    data_dir, out_dir = Path(args.data_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)

    deep = load(data_dir)
    eu = deep[deep.group == "eu"]
    ref_all = deep[deep.group == "referencia"]
    ref = ref_all[ref_all.opp_is_tracked == 0]
    res = {"n": {"eu": len(eu), "ref": len(ref), "ref_total": len(ref_all),
                 "ref_mirrored_rows": int(ref_all.opp_is_tracked.sum()),
                 "mirrored_matches": int(ref_all.opp_is_tracked.sum() // 2)}}

    # 1) curva minuto a minuto
    gd_e = eu[[f"gd_{m}" for m in MINUTES]].values.astype(float)
    gd_r = ref[[f"gd_{m}" for m in MINUTES]].values.astype(float)
    be, br = boot_means(gd_e, rng), boot_means(gd_r, rng)
    gap_b = br - be
    gap = np.nanmean(gd_r, axis=0) - np.nanmean(gd_e, axis=0)
    gap_lo, gap_hi = np.percentile(gap_b, 2.5, axis=0), np.percentile(gap_b, 97.5, axis=0)
    first_clear = next(m for m in MINUTES if all(gap_lo[k] > 0 for k in range(m - 1, 15)))
    res["curve"] = {
        "eu_mean": np.nanmean(gd_e, axis=0).round(1).tolist(), "ref_mean": np.nanmean(gd_r, axis=0).round(1).tolist(),
        "eu_lo": np.percentile(be, 2.5, axis=0).round(1).tolist(), "eu_hi": np.percentile(be, 97.5, axis=0).round(1).tolist(),
        "ref_lo": np.percentile(br, 2.5, axis=0).round(1).tolist(), "ref_hi": np.percentile(br, 97.5, axis=0).round(1).tolist(),
        "gap": gap.round(1).tolist(), "gap_lo": gap_lo.round(1).tolist(), "gap_hi": gap_hi.round(1).tolist(),
        "eu_median": np.nanmedian(gd_e, axis=0).round(0).tolist(), "ref_median": np.nanmedian(gd_r, axis=0).round(0).tolist(),
    }
    res["first_clear_minute"] = int(first_clear)
    res["gold_growth_per_min_5_to_15"] = round(float((gap[14] - gap[4]) / 10), 1)

    # 2) decomposição aos 15 min
    d = pd.concat([eu, ref]).dropna(subset=["gd_15"] + FEATS)
    e_, r_ = d[d.group == "eu"], d[d.group == "referencia"]
    beta, r2, contrib, rest, gap_total = decompose(e_, r_)
    boots = {f: [] for f in FEATS}; boots["rest"] = []
    for _ in range(1500):
        be_i = e_.iloc[rng.integers(0, len(e_), len(e_))]
        br_i = r_.iloc[rng.integers(0, len(r_), len(r_))]
        _, _, c_i, rest_i, _ = decompose(be_i, br_i)
        for f in FEATS:
            boots[f].append(c_i[f])
        boots["rest"].append(rest_i)
    parts = []
    for f in FEATS + ["rest"]:
        v = rest if f == "rest" else contrib[f]
        lo, hi = np.percentile(boots[f], [2.5, 97.5])
        parts.append({"key": f, "label": "Resto (passivo, objetivos, não explicado)" if f == "rest" else FEAT_LABELS[f],
                      "value": round(float(v), 1), "lo": round(float(lo), 1), "hi": round(float(hi), 1),
                      "share_pct": round(100 * float(v) / gap_total, 1)})
    res["decomposition"] = {"gap_total": round(float(gap_total), 1), "r2": round(float(r2), 3), "n_rows": len(d),
                            "gold_per_cs": round(float(beta[1]), 1), "gold_per_killgold": round(float(beta[2]), 2),
                            "gold_per_assist_diff": round(float(beta[3]), 1), "gold_per_plate_diff": round(float(beta[4]), 1),
                            "parts": parts}

    # 3) primeiras trocas e hábitos de rota
    early_specs = {"kill_by_5": "Abate até o min 5 (% das partidas)", "death_by_5": "Morte até o min 5 (% das partidas)",
                   "cd_5": "CS à frente do adversário no min 5", "xd_5": "XP à frente do adversário no min 5",
                   "wards_me_15": "Sentinelas colocadas até o min 15", "wardkills_me_15": "Sentinelas destruídas até o min 15",
                   "kd_net_15": "Abates − mortes até o min 15", "pl_diff": "Placas: você − adversário (até o min 15)",
                   "first_kill_s": "Segundo do primeiro abate (mediana, só quem teve)", "first_back_s": "Segundo do primeiro back"}
    early = {}
    for k in early_specs:
        a, b = eu[k].astype(float), ref[k].astype(float)
        if k in ("first_kill_s", "first_back_s"):
            lo, hi = boot_diff(b, a, rng)
            early[k] = {"eu": round(float(a.median()), 1), "ref": round(float(b.median()), 1)}
        else:
            lo, hi = boot_diff(b, a, rng)
            early[k] = {"eu": round(float(a.mean()), 2), "ref": round(float(b.mean()), 2)}
        early[k].update({"diff_ref_minus_eu": round(float(b.mean() - a.mean()), 2),
                         "ci_lo": round(float(lo), 2), "ci_hi": round(float(hi), 2),
                         "clear": bool(lo > 0 or hi < 0), "label": early_specs[k]})
    res["early"] = early

    # 4) tabela geral de métricas (partida inteira / 15 min)
    table_specs = {"cs_per_min": "CS por minuto", "cs_15": "CS aos 15 min", "gd_15": "Diferença de ouro aos 15 min",
                   "xd_15": "Diferença de XP aos 15 min", "kills_me_15": "Abates até o min 15",
                   "deaths_me_10": "Mortes até o min 10", "deaths_me_15": "Mortes até o min 15",
                   "kp_pct": "Participação em abates (%)", "damage_per_min": "Dano por minuto",
                   "gold_per_min": "Ouro por minuto", "vision_score": "Pontuação de visão"}
    table = {}
    for k, label in table_specs.items():
        a, b = eu[k].astype(float), ref[k].astype(float)
        lo, hi = boot_diff(b, a, rng)
        table[k] = {"label": label, "eu": round(float(a.mean()), 2), "ref": round(float(b.mean()), 2),
                    "diff_ref_minus_eu": round(float(b.mean() - a.mean()), 2),
                    "ci_lo": round(float(lo), 2), "ci_hi": round(float(hi), 2), "clear": bool(lo > 0 or hi < 0)}
    res["table"] = table

    # 5) o que prevê a vitória (1 linha por partida)
    d1 = deep.drop_duplicates("match_id")
    res["auc_gold_diff_by_minute"] = {str(m): round(auc(d1[f"gd_{m}"], d1.win), 3) for m in (3, 5, 8, 10, 12, 15)}
    ahead, behind = d1[d1.gd_8 > 300], d1[d1.gd_8 < -300]
    res["lane_lead_min8"] = {
        "ahead_300": {"n": len(ahead), "winrate": round(float(ahead.win.mean()), 3)},
        "behind_300": {"n": len(behind), "winrate": round(float(behind.win.mean()), 3)},
        "share_games_ahead": {"eu": round(float((eu.gd_8 > 300).mean()), 3), "ref": round(float((ref.gd_8 > 300).mean()), 3)},
        "share_games_behind": {"eu": round(float((eu.gd_8 < -300).mean()), 3), "ref": round(float((ref.gd_8 < -300).mean()), 3)},
    }
    res["winrate"] = {"eu": round(float(eu.win.mean()), 3), "ref": round(float(ref.win.mean()), 3)}

    Path("results.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    fig_curve(res, out_dir / "fig1_gold_curve.png", res["n"]["eu"], res["n"]["ref"])
    fig_decomp(res, out_dir / "fig2_decomposition.png")
    fig_early(res, out_dir / "fig3_early_game.png")

    print(f"Figuras em {out_dir}/ e números em results.json")
    print(f"Diferença clara a partir do minuto {first_clear}; R² da decomposição = {r2:.2f}")


if __name__ == "__main__":
    main()
