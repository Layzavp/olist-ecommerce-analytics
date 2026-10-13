-- =====================================================================
-- 01_views.sql  |  Camada limpa sobre as tabelas brutas do Olist
-- Regra de ouro: agregar ANTES de juntar (itens, pagamentos e reviews
-- têm várias linhas por pedido; juntar direto multiplica a receita).
-- =====================================================================

DROP VIEW IF EXISTS vw_reviews_latest;
DROP VIEW IF EXISTS vw_order_items_agg;
DROP VIEW IF EXISTS vw_order_payments_agg;
DROP VIEW IF EXISTS vw_items_enriched;
DROP VIEW IF EXISTS vw_order_facts;

-- 1 review por pedido (mantém a mais recente; há pedidos com >1 review)
CREATE VIEW vw_reviews_latest AS
SELECT order_id, review_score, review_comment_message, review_creation_date
FROM (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY review_answer_timestamp DESC, review_id
           ) AS rn
    FROM order_reviews r
)
WHERE rn = 1;

-- Itens agregados por pedido
CREATE VIEW vw_order_items_agg AS
SELECT order_id,
       COUNT(*)                       AS qtd_itens,
       COUNT(DISTINCT seller_id)      AS qtd_sellers,
       SUM(price)                     AS receita_itens,
       SUM(freight_value)             AS frete_total,
       SUM(price + freight_value)     AS valor_total
FROM order_items
GROUP BY order_id;

-- Pagamentos agregados por pedido
CREATE VIEW vw_order_payments_agg AS
SELECT order_id,
       SUM(payment_value)             AS valor_pago,
       MAX(payment_installments)      AS max_parcelas,
       COUNT(DISTINCT payment_type)   AS qtd_tipos_pgto
FROM order_payments
GROUP BY order_id;

-- Item a item, já com categoria em inglês e dados do vendedor
CREATE VIEW vw_items_enriched AS
SELECT i.order_id, i.order_item_id, i.product_id, i.seller_id,
       i.price, i.freight_value,
       COALESCE(t.product_category_name_english,
                p.product_category_name, 'unknown') AS categoria,
       s.seller_state, s.seller_city
FROM order_items i
JOIN products p              ON p.product_id = i.product_id
LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
JOIN sellers s               ON s.seller_id = i.seller_id;

-- Tabela fato: 1 linha por pedido (grão = order_id)
CREATE VIEW vw_order_facts AS
SELECT o.order_id,
       c.customer_unique_id,
       c.customer_state,
       c.customer_city,
       o.order_status,
       DATE(o.order_purchase_timestamp)              AS data_compra,
       STRFTIME('%Y-%m', o.order_purchase_timestamp) AS ano_mes,
       o.order_delivered_customer_date               AS data_entrega,
       o.order_estimated_delivery_date               AS data_estimada,
       -- dias entre compra e entrega (só entregues com data válida)
       CASE WHEN o.order_status = 'delivered'
             AND o.order_delivered_customer_date IS NOT NULL
            THEN ROUND(JULIANDAY(o.order_delivered_customer_date)
                     - JULIANDAY(o.order_purchase_timestamp), 1) END AS dias_entrega,
       -- dias de atraso (positivo = atrasou)
       CASE WHEN o.order_status = 'delivered'
             AND o.order_delivered_customer_date IS NOT NULL
            THEN ROUND(JULIANDAY(o.order_delivered_customer_date)
                     - JULIANDAY(o.order_estimated_delivery_date), 1) END AS dias_atraso,
       CASE WHEN o.order_status = 'delivered'
             AND o.order_delivered_customer_date IS NOT NULL
            THEN (o.order_delivered_customer_date
                    > o.order_estimated_delivery_date) END          AS flag_atrasado,
       a.qtd_itens, a.qtd_sellers, a.receita_itens, a.frete_total, a.valor_total,
       p.valor_pago, p.max_parcelas,
       r.review_score
FROM orders o
JOIN customers c                 ON c.customer_id = o.customer_id
LEFT JOIN vw_order_items_agg a   ON a.order_id = o.order_id
LEFT JOIN vw_order_payments_agg p ON p.order_id = o.order_id
LEFT JOIN vw_reviews_latest r    ON r.order_id = o.order_id;
