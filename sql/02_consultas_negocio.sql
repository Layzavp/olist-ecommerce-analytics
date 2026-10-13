-- =====================================================================
-- 02_consultas_negocio.sql  |  Perguntas de negócio (SQLite)
-- Escopo: pedidos entregues, jan/2017 a ago/2018 (meses completos).
-- =====================================================================

-- Q1. Receita, pedidos e ticket médio por mês, com crescimento MoM
WITH mensal AS (
    SELECT ano_mes,
           COUNT(*)                         AS pedidos,
           ROUND(SUM(receita_itens), 2)     AS receita,
           ROUND(SUM(receita_itens) / COUNT(*), 2) AS ticket_medio
    FROM vw_order_facts
    WHERE order_status = 'delivered'
      AND ano_mes BETWEEN '2017-01' AND '2018-08'
    GROUP BY ano_mes
)
SELECT ano_mes, pedidos, receita, ticket_medio,
       ROUND(100.0 * (receita - LAG(receita) OVER (ORDER BY ano_mes))
                    / LAG(receita) OVER (ORDER BY ano_mes), 1) AS crescimento_mom_pct
FROM mensal
ORDER BY ano_mes;

-- Q2. Top 10 categorias por receita, com participação acumulada
WITH cat AS (
    SELECT e.categoria,
           COUNT(DISTINCT e.order_id) AS pedidos,
           ROUND(SUM(e.price), 2)     AS receita
    FROM vw_items_enriched e
    JOIN vw_order_facts f USING (order_id)
    WHERE f.order_status = 'delivered'
      AND f.ano_mes BETWEEN '2017-01' AND '2018-08'
    GROUP BY e.categoria
)
SELECT categoria, pedidos, receita,
       ROUND(100.0 * receita / SUM(receita) OVER (), 1)                        AS pct_receita,
       ROUND(100.0 * SUM(receita) OVER (ORDER BY receita DESC) / SUM(receita) OVER (), 1) AS pct_acumulado
FROM cat
ORDER BY receita DESC
LIMIT 10;

-- Q3. Logística por estado do cliente: prazo, atraso e nota
SELECT customer_state                                   AS uf,
       COUNT(*)                                         AS pedidos,
       ROUND(AVG(dias_entrega), 1)                      AS dias_entrega_medio,
       ROUND(100.0 * AVG(flag_atrasado), 1)             AS pct_atrasados,
       ROUND(100.0 * SUM(frete_total) / SUM(receita_itens), 1) AS frete_pct_da_receita,
       ROUND(AVG(review_score), 2)                      AS nota_media
FROM vw_order_facts
WHERE order_status = 'delivered' AND dias_entrega IS NOT NULL
  AND ano_mes BETWEEN '2017-01' AND '2018-08'
GROUP BY customer_state
HAVING pedidos >= 200
ORDER BY pct_atrasados DESC;

-- Q4. Atraso derruba a nota? Nota média por faixa de atraso
-- Regra desta consulta: usa dias_atraso arredondado a 1 casa decimal. Atrasos de poucos minutos
-- (61 pedidos) podem cair em "No prazo". O notebook 01/Excel seguem esta regra; a medida
-- % Atraso do Power BI usa flag_atrasado (comparação exata de data/hora), portanto os totais
-- podem diferir. Documente a medida específica usada em cada visual/cartão.
SELECT CASE WHEN dias_atraso <= 0  THEN '1. No prazo'
            WHEN dias_atraso <= 3  THEN '2. Atraso 1-3 dias'
            WHEN dias_atraso <= 7  THEN '3. Atraso 4-7 dias'
            ELSE                        '4. Atraso > 7 dias' END AS faixa,
       COUNT(*)                                         AS pedidos,
       ROUND(AVG(review_score), 2)                      AS nota_media,
       ROUND(100.0 * AVG(review_score <= 2), 1)         AS pct_notas_baixas
FROM vw_order_facts
WHERE order_status = 'delivered' AND dias_atraso IS NOT NULL AND review_score IS NOT NULL
  AND ano_mes BETWEEN '2017-01' AND '2018-08'
GROUP BY faixa
ORDER BY faixa;

-- Q5. Concentração de vendedores (Pareto): % da receita nos top 20% vendedores
WITH s AS (
    SELECT i.seller_id, SUM(i.price) AS receita
    FROM order_items i
    JOIN vw_order_facts f USING (order_id)
    WHERE f.order_status = 'delivered'
      AND f.ano_mes BETWEEN '2017-01' AND '2018-08'
    GROUP BY i.seller_id
), r AS (
    SELECT seller_id, receita,
           NTILE(5) OVER (ORDER BY receita DESC) AS quintil
    FROM s
)
SELECT quintil,
       COUNT(*)                                         AS vendedores,
       ROUND(SUM(receita), 2)                           AS receita,
       ROUND(100.0 * SUM(receita) / (SELECT SUM(receita) FROM s), 1) AS pct_receita
FROM r
GROUP BY quintil
ORDER BY quintil;

-- Q6. Recompra: clientes por número de pedidos entregues
WITH c AS (
    SELECT customer_unique_id, COUNT(*) AS n_pedidos
    FROM vw_order_facts
    WHERE order_status = 'delivered'
      AND ano_mes BETWEEN '2017-01' AND '2018-08'
    GROUP BY customer_unique_id
)
SELECT CASE WHEN n_pedidos >= 3 THEN '3+' ELSE CAST(n_pedidos AS TEXT) END AS pedidos,
       COUNT(*)                                          AS clientes,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM c
GROUP BY 1
ORDER BY 1;

-- Q7. Base do RFM (1 linha por cliente) — pontuação final feita no Python
SELECT customer_unique_id,
       -- referência = dia seguinte à última compra do escopo (igual ao Python)
       CAST(JULIANDAY((SELECT MAX(data_compra) FROM vw_order_facts
                       WHERE order_status = 'delivered' AND ano_mes BETWEEN '2017-01' AND '2018-08'), '+1 day')
            - JULIANDAY(MAX(data_compra)) AS INT) AS recencia_dias,
       COUNT(*)                          AS frequencia,
       ROUND(SUM(receita_itens), 2)      AS monetario
FROM vw_order_facts
WHERE order_status = 'delivered'
  AND ano_mes BETWEEN '2017-01' AND '2018-08'
GROUP BY customer_unique_id;
