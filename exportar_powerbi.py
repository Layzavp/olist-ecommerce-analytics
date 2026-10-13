"""Exporta o modelo dimensional (estrela) para o Power BI em CSV.

Mesmo escopo do notebook: pedidos 'delivered', compra entre 2017-01 e 2018-08.
Uso: python exportar_powerbi.py   (requer olist.db gerado por carregar_banco.py)
"""
import os
import sqlite3
from pathlib import Path
import sys
import numpy as np
import pandas as pd

os.chdir(Path(__file__).resolve().parent)  # caminhos relativos à raiz do projeto

sys.path.insert(0, "src")
from categorias_pt import traduzir

OUT = Path("powerbi/dados"); OUT.mkdir(parents=True, exist_ok=True)
con = sqlite3.connect("olist.db")

fatos = pd.read_sql("SELECT * FROM vw_order_facts", con, parse_dates=["data_compra"])
base = fatos[(fatos.order_status == "delivered") & fatos.ano_mes.between("2017-01", "2018-08")].copy()

# ---------- fato_pedidos (grão: 1 linha por pedido) ----------
base["prazo_estimado_dias"] = (pd.to_datetime(base.data_estimada) - base.data_compra).dt.days
base["faixa_atraso"] = np.select(
    [base.dias_atraso.isna(), base.dias_atraso <= 0, base.dias_atraso <= 3, base.dias_atraso <= 7],
    ["Sem data de entrega", "1. No prazo", "2. Atraso 1-3 dias", "3. Atraso 4-7 dias"], default="4. Atraso > 7 dias")
fato_pedidos = base.rename(columns={"customer_state": "uf"})[[
    "order_id", "customer_unique_id", "uf", "data_compra", "dias_entrega", "dias_atraso",
    "flag_atrasado", "faixa_atraso", "prazo_estimado_dias", "qtd_itens", "receita_itens",
    "frete_total", "review_score"]]
# inteiros anuláveis: o CSV grava 1 (e não 1.0), e vazio onde não há dado
for c in ["flag_atrasado", "review_score", "qtd_itens"]:
    fato_pedidos[c] = fato_pedidos[c].astype("Int64")
fato_pedidos.to_csv(OUT / "fato_pedidos.csv", index=False, date_format="%Y-%m-%d")

# ---------- fato_itens (grão: 1 linha por item de pedido) ----------
itens = pd.read_sql("SELECT order_id, order_item_id, product_id, seller_id, price, freight_value FROM order_items", con)
fato_itens = itens.merge(base[["order_id"]], on="order_id")
fato_itens.to_csv(OUT / "fato_itens.csv", index=False)

# ---------- dim_produtos e dim_vendedores ----------
enr = pd.read_sql("SELECT DISTINCT product_id, categoria FROM vw_items_enriched", con)
dim_produtos = enr[enr.product_id.isin(fato_itens.product_id)].drop_duplicates("product_id").copy()
dim_produtos["categoria"] = traduzir(dim_produtos.categoria)
dim_produtos.to_csv(OUT / "dim_produtos.csv", index=False)

sel = pd.read_sql("SELECT seller_id, seller_city, seller_state FROM sellers", con)
dim_vendedores = sel[sel.seller_id.isin(fato_itens.seller_id)]
dim_vendedores.to_csv(OUT / "dim_vendedores.csv", index=False)

# ---------- dim_uf (estados e regiões) ----------
UF = {"AC":("Acre","Norte"),"AP":("Amapá","Norte"),"AM":("Amazonas","Norte"),"PA":("Pará","Norte"),
 "RO":("Rondônia","Norte"),"RR":("Roraima","Norte"),"TO":("Tocantins","Norte"),
 "AL":("Alagoas","Nordeste"),"BA":("Bahia","Nordeste"),"CE":("Ceará","Nordeste"),"MA":("Maranhão","Nordeste"),
 "PB":("Paraíba","Nordeste"),"PE":("Pernambuco","Nordeste"),"PI":("Piauí","Nordeste"),
 "RN":("Rio Grande do Norte","Nordeste"),"SE":("Sergipe","Nordeste"),
 "DF":("Distrito Federal","Centro-Oeste"),"GO":("Goiás","Centro-Oeste"),"MT":("Mato Grosso","Centro-Oeste"),
 "MS":("Mato Grosso do Sul","Centro-Oeste"),
 "ES":("Espírito Santo","Sudeste"),"MG":("Minas Gerais","Sudeste"),"RJ":("Rio de Janeiro","Sudeste"),
 "SP":("São Paulo","Sudeste"),"PR":("Paraná","Sul"),"RS":("Rio Grande do Sul","Sul"),"SC":("Santa Catarina","Sul")}
dim_uf = pd.DataFrame([(k, v[0], v[1], "Brasil") for k, v in UF.items()], columns=["uf", "estado", "regiao", "pais"])
assert set(fato_pedidos.uf) <= set(dim_uf.uf), "UF sem mapeamento"
dim_uf.to_csv(OUT / "dim_uf.csv", index=False)

# ---------- dim_clientes (RFM) — mesma lógica do notebook ----------
cli = (base.groupby("customer_unique_id")
       .agg(frequencia=("order_id", "count"), monetario=("receita_itens", "sum"), ultima=("data_compra", "max")))
ref = base.data_compra.max() + pd.Timedelta(days=1)
cli["recencia_dias"] = (ref - cli.ultima).dt.days
cli["R"] = pd.qcut(cli.recencia_dias.rank(method="first"), 5, labels=[5, 4, 3, 2, 1]).astype(int)
cli["M"] = pd.qcut(cli.monetario.rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
def seg(r):
    if r.frequencia >= 2: return "Recorrentes"
    if r.R >= 4 and r.M >= 4: return "Novos de alto valor"
    if r.R >= 4: return "Novos"
    if r.R <= 2 and r.M >= 4: return "Inativos de alto valor"
    if r.R <= 2: return "Inativos"
    return "Intermediários"
cli["segmento"] = cli.apply(seg, axis=1)
cli["monetario"] = cli.monetario.round(2)
cli.drop(columns="ultima").reset_index().to_csv(OUT / "dim_clientes.csv", index=False)

# ---------- relatório de integridade ----------
print("fato_pedidos :", f"{len(fato_pedidos):,}", "| fato_itens:", f"{len(fato_itens):,}")
print("dim_produtos :", len(dim_produtos), "| dim_vendedores:", len(dim_vendedores), "| dim_clientes:", len(cli), "| dim_uf:", len(dim_uf))
print("chaves únicas -> pedidos:", fato_pedidos.order_id.is_unique, "| produtos:", dim_produtos.product_id.is_unique,
      "| vendedores:", dim_vendedores.seller_id.is_unique, "| clientes:", cli.index.is_unique)
print("órfãos -> itens sem produto:", (~fato_itens.product_id.isin(dim_produtos.product_id)).sum(),
      "| itens sem vendedor:", (~fato_itens.seller_id.isin(dim_vendedores.seller_id)).sum(),
      "| pedidos sem cliente:", (~fato_pedidos.customer_unique_id.isin(cli.index)).sum())
print("pedidos sem nenhum item:", (~fato_pedidos.order_id.isin(fato_itens.order_id)).sum())
print("receita pedidos vs itens:", round(fato_pedidos.receita_itens.sum(), 2), round(fato_itens.price.sum(), 2))

# ---------- valores de conferência para validar o dashboard ----------
p = fato_pedidos
ent = p.dropna(subset=["flag_atrasado"])
baixas = p[p.review_score <= 2]
vend = fato_itens.groupby("seller_id").price.sum().sort_values(ascending=False)
n20 = int(np.ceil(len(vend) * 0.2))
r17 = p[(p.data_compra >= "2017-01-01") & (p.data_compra <= "2017-08-31")].receita_itens.sum()
r18 = p[(p.data_compra >= "2018-01-01") & (p.data_compra <= "2018-08-31")].receita_itens.sum()
ref = {
 "Receita": round(p.receita_itens.sum(), 2), "Pedidos": len(p), "Ticket medio": round(p.receita_itens.sum()/len(p), 2),
 "Frete % receita": round(p.frete_total.sum()/p.receita_itens.sum()*100, 1),
 "Entrega media (dias)": round(p.dias_entrega.mean(), 1), "% atraso": round(ent.flag_atrasado.mean()*100, 1),
 "Nota media": round(p.review_score.mean(), 2), "% notas baixas": round((p.review_score.dropna() <= 2).mean()*100, 1),
 "% das notas baixas vindas de atrasos": round(baixas.flag_atrasado.sum()/len(baixas)*100, 1),
 "Clientes unicos": p.customer_unique_id.nunique(), "Taxa de recompra %": round((cli.frequencia >= 2).mean()*100, 1),
 "Vendedores": len(vend), f"Top 20% vendedores (n={n20}) % receita": round(vend.head(n20).sum()/vend.sum()*100, 1),
 "YoY jan-ago 2018 vs 2017 %": round((r18/r17-1)*100, 1),
}
print("\nVALORES DE CONFERENCIA"); [print(f"  {k}: {v}") for k, v in ref.items()]
