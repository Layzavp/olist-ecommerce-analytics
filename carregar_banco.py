"""Carrega os 9 CSVs do Olist em um banco SQLite e cria as views limpas.

Uso:
    1. Baixe o dataset no Kaggle (olistbr/brazilian-ecommerce) e extraia os CSVs em ./data
    2. python carregar_banco.py
"""
import os
import sqlite3
from pathlib import Path
import pandas as pd

os.chdir(Path(__file__).resolve().parent)  # caminhos relativos à raiz do projeto

DATA = Path("data")
DB = Path("olist.db")
TABELAS = {
    "olist_customers_dataset": "customers",
    "olist_geolocation_dataset": "geolocation",
    "olist_order_items_dataset": "order_items",
    "olist_order_payments_dataset": "order_payments",
    "olist_order_reviews_dataset": "order_reviews",
    "olist_orders_dataset": "orders",
    "olist_products_dataset": "products",
    "olist_sellers_dataset": "sellers",
    "product_category_name_translation": "category_translation",
}

def main():
    con = sqlite3.connect(DB)
    for arquivo, tabela in TABELAS.items():
        df = pd.read_csv(DATA / f"{arquivo}.csv")
        df.to_sql(tabela, con, if_exists="replace", index=False)
        print(f"{tabela:22s} {len(df):>9,} linhas")
    # índices nas chaves de junção
    for tabela, coluna in [("orders", "order_id"), ("orders", "customer_id"),
                           ("order_items", "order_id"), ("order_items", "product_id"),
                           ("order_items", "seller_id"), ("order_payments", "order_id"),
                           ("order_reviews", "order_id"), ("customers", "customer_id")]:
        con.execute(f"CREATE INDEX IF NOT EXISTS ix_{tabela}_{coluna} ON {tabela}({coluna})")
    con.executescript(Path("sql/01_views.sql").read_text())
    con.commit()
    con.close()
    print("\nBanco criado:", DB)

if __name__ == "__main__":
    main()
