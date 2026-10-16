-- =====================================================================
-- Consultas SQL sobre data/lol.db (tabela "matches", gerada por build_db.py)
-- Uma linha por jogador e partida. Para rodar tudo: python run_queries.py
--
-- Observacao: "group" e palavra reservada em SQL, por isso aparece entre aspas.
-- =====================================================================

-- Setup: ADCs "limpos" = ADCs (BOTTOM) sem as partidas espelhadas
-- (aquelas em que dois jogadores de referencia se enfrentaram).
DROP VIEW IF EXISTS adc_clean;

CREATE VIEW adc_clean AS
SELECT *
FROM matches
WHERE position = 'BOTTOM'
  AND match_id NOT IN (
        SELECT match_id
        FROM matches
        WHERE position = 'BOTTOM' AND "group" = 'referencia'
        GROUP BY match_id
        HAVING COUNT(*) > 1
  );

-- Q1. Visao geral: eu vs referencia (agregacao simples)
SELECT "group"                       AS grupo,
       COUNT(*)                      AS partidas,
       ROUND(100.0 * AVG(win), 1)    AS winrate_pct,
       ROUND(AVG(cs_per_min), 2)     AS cs_por_min,
       ROUND(AVG(gold_diff_15), 0)   AS dif_ouro_15,
       ROUND(AVG(cs_diff_15), 1)     AS dif_cs_15,
       ROUND(AVG(damage_per_min), 0) AS dano_por_min,
       ROUND(AVG(vision_score), 1)   AS visao
FROM adc_clean
GROUP BY "group"
ORDER BY "group";

-- Q2. Winrate por faixa de diferenca de ouro aos 15 min (CASE + subconsulta)
SELECT faixa,
       COUNT(*)                   AS partidas,
       ROUND(100.0 * AVG(win), 1) AS winrate_pct
FROM (
    SELECT win,
           CASE
               WHEN gold_diff_15 < -1500 THEN '1) abaixo de -1500'
               WHEN gold_diff_15 < -500  THEN '2) -1500 a -500'
               WHEN gold_diff_15 <  500  THEN '3) -500 a +500'
               WHEN gold_diff_15 <  1500 THEN '4) +500 a +1500'
               ELSE '5) acima de +1500'
           END AS faixa
    FROM adc_clean
    WHERE gold_diff_15 IS NOT NULL
)
GROUP BY faixa
ORDER BY faixa;

-- Q3. Estar a frente na rota aos 15 min: quanto vale para cada grupo? (janela sobre agregado)
SELECT "group" AS grupo,
       CASE WHEN gold_diff_15 > 0 THEN 'a frente' ELSE 'atras ou igual' END AS situacao,
       COUNT(*) AS partidas,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY "group"), 1) AS pct_dos_jogos,
       ROUND(100.0 * AVG(win), 1) AS winrate_pct
FROM adc_clean
WHERE gold_diff_15 IS NOT NULL
GROUP BY "group", situacao
ORDER BY "group", situacao;

-- Q4. Campeoes com amostra minima (3+ partidas), por grupo (HAVING)
SELECT "group"                       AS grupo,
       champion                      AS campeao,
       COUNT(*)                      AS partidas,
       ROUND(100.0 * AVG(win), 1)    AS winrate_pct,
       ROUND(AVG(gold_diff_15), 0)   AS dif_ouro_15
FROM adc_clean
GROUP BY "group", champion
HAVING COUNT(*) >= 3
ORDER BY "group", partidas DESC, campeao;

-- Q5. Top 3 campeoes mais jogados de cada grupo (ROW_NUMBER por particao)
SELECT grupo, campeao, partidas, winrate_pct
FROM (
    SELECT "group"                    AS grupo,
           champion                   AS campeao,
           COUNT(*)                   AS partidas,
           ROUND(100.0 * AVG(win), 1) AS winrate_pct,
           ROW_NUMBER() OVER (PARTITION BY "group" ORDER BY COUNT(*) DESC, champion) AS pos
    FROM adc_clean
    GROUP BY "group", champion
)
WHERE pos <= 3
ORDER BY grupo, pos;

-- Q6. Meu desempenho por periodo do dia, horario de Brasilia (CASE + GROUP BY)
SELECT CASE
           WHEN hour < 6  THEN '1) madrugada (0h-5h)'
           WHEN hour < 12 THEN '2) manha (6h-11h)'
           WHEN hour < 18 THEN '3) tarde (12h-17h)'
           ELSE                '4) noite (18h-23h)'
       END                         AS periodo,
       COUNT(*)                    AS partidas,
       ROUND(100.0 * AVG(win), 1)  AS winrate_pct,
       ROUND(AVG(deaths), 1)       AS mortes_media
FROM adc_clean
WHERE "group" = 'eu'
GROUP BY periodo
ORDER BY periodo;

-- Q7. Minhas ultimas 10 partidas com winrate movel das 10 mais recentes (janela deslizante)
WITH ordenadas AS (
    SELECT date,
           champion,
           win,
           ROUND(100.0 * AVG(win) OVER (ORDER BY date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW), 1)
               AS winrate_movel_10
    FROM adc_clean
    WHERE "group" = 'eu'
)
SELECT *
FROM ordenadas
ORDER BY date DESC
LIMIT 10;

-- Q8. Primeiro dragao do time e resultado, por grupo
SELECT "group"                    AS grupo,
       team_first_dragon          AS time_pegou_primeiro_dragao,
       COUNT(*)                   AS partidas,
       ROUND(100.0 * AVG(win), 1) AS winrate_pct
FROM adc_clean
GROUP BY "group", team_first_dragon
ORDER BY "group", team_first_dragon;

-- Q9. Cada jogador lado a lado: eu e as referencias anonimizadas (ordenado por ouro aos 15 min)
SELECT player                                 AS jogador,
       COUNT(*)                               AS partidas,
       ROUND(100.0 * AVG(win), 1)             AS winrate_pct,
       ROUND(AVG(gold_diff_15), 0)            AS dif_ouro_15,
       ROUND(AVG(cs_diff_15), 1)              AS dif_cs_15,
       ROUND(AVG(deaths_15), 1)               AS mortes_ate_15,
       ROUND(100.0 * AVG(kill_participation), 1) AS part_abates_pct
FROM adc_clean
GROUP BY player
ORDER BY dif_ouro_15 DESC;
