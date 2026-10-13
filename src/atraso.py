"""Modelo de risco de atraso na entrega — atributos e avaliação sem vazamento de dados.

Princípios:
  1. Só entram atributos conhecidos NO MOMENTO DA COMPRA (nada de datas de entrega, nota ou status).
  2. Validação temporal em janelas móveis: treina com o passado, testa no mês seguinte.
  3. Em cada data de corte, o treino só inclui pedidos cujo desfecho já era conhecido
     (entregues antes do corte): é o que existiria em produção.
"""
import warnings
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------------------------
# 1. Montagem do dataset (1 linha por pedido entregue, jan/2017 a ago/2018)
# ----------------------------------------------------------------------------------
def montar_dataset(con) -> pd.DataFrame:
    q = lambda s: pd.read_sql(s, con)
    o = q("""SELECT o.order_id, c.customer_state, c.customer_zip_code_prefix AS cz, o.order_status,
                    o.order_purchase_timestamp AS ts, o.order_estimated_delivery_date AS est,
                    o.order_delivered_customer_date AS ent
             FROM orders o JOIN customers c USING (customer_id)""")
    for c in ["ts", "est", "ent"]:
        o[c] = pd.to_datetime(o[c])

    # congestionamento da plataforma: pedidos (todos os status) nos 7 dias ANTERIORES à compra
    t = np.sort(o.ts.values)
    o["pedidos_7d"] = (np.searchsorted(t, o.ts.values, side="left")
                       - np.searchsorted(t, (o.ts - pd.Timedelta(days=7)).values, side="left"))

    it = q("""SELECT i.order_id, i.seller_id, i.price, i.freight_value, p.product_weight_g AS w,
                     p.product_length_cm * p.product_height_cm * p.product_width_cm AS vol,
                     COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS cat,
                     s.seller_state, s.seller_zip_code_prefix AS sz
              FROM order_items i
              JOIN products p USING (product_id) JOIN sellers s USING (seller_id)
              LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name""")
    it = it.sort_values(["order_id", "price"], ascending=[True, False])
    principal = it.drop_duplicates("order_id")[["order_id", "seller_id", "cat", "seller_state", "sz"]]
    agg = it.groupby("order_id").agg(n_itens=("price", "size"), n_sellers=("seller_id", "nunique"),
                                     preco=("price", "sum"), frete=("freight_value", "sum"),
                                     peso_g=("w", "sum"), vol_cm3=("vol", "sum"), peso_max=("w", "max")).reset_index()
    d = o.merge(agg, on="order_id").merge(principal, on="order_id")

    # distância vendedor–cliente (haversine) a partir do CEP (prefixo)
    g = q("""SELECT geolocation_zip_code_prefix AS z, AVG(geolocation_lat) AS lat, AVG(geolocation_lng) AS lng
             FROM geolocation GROUP BY 1""")
    d = (d.merge(g.rename(columns={"z": "cz", "lat": "clat", "lng": "clng"}), on="cz", how="left")
          .merge(g.rename(columns={"z": "sz", "lat": "slat", "lng": "slng"}), on="sz", how="left"))
    p1, p2, dl = np.radians(d.clat), np.radians(d.slat), np.radians(d.clng - d.slng)
    d["dist_km"] = 6371 * np.arccos(np.clip(np.sin(p1) * np.sin(p2) + np.cos(p1) * np.cos(p2) * np.cos(dl), -1, 1))

    d["mesmo_estado"] = (d.customer_state == d.seller_state).astype(int)
    d["prazo_prometido"] = (d.est - d.ts).dt.total_seconds() / 86400          # dias prometidos ao cliente
    d["frete_pct"] = d.frete / d.preco
    d["dow"], d["hora"] = d.ts.dt.dayofweek, d.ts.dt.hour

    # carga do vendedor: pedidos dele nos 14 dias anteriores
    d["seller_14d"] = 0.0
    for _, idx in d.groupby("seller_id").groups.items():
        tv = d.loc[idx, "ts"].values
        ts_ = np.sort(tv)
        d.loc[idx, "seller_14d"] = (np.searchsorted(ts_, tv, side="left")
                                    - np.searchsorted(ts_, tv - np.timedelta64(14, "D"), side="left"))

    d["ym"] = d.ts.dt.strftime("%Y-%m")
    d = d[(d.order_status == "delivered") & d.ent.notna() & d.ym.between("2017-01", "2018-08")].copy()
    d["y"] = (d.ent > d.est).astype(int)                                          # 1 = entregue com atraso
    d["dias_real"] = (d.ent - d.ts).dt.total_seconds() / 86400                    # só p/ treino (desfecho)
    d["rota"] = d.seller_state.astype(str) + ">" + d.customer_state.astype(str)
    return d.sort_values("ts").reset_index(drop=True)


# ----------------------------------------------------------------------------------
# 2. Tempo típico da rota e "folga" do prazo prometido (aprendido só no treino)
# ----------------------------------------------------------------------------------
def tempo_tipico(treino, df, t0, dias=None):
    """Mediana de dias de entrega por rota (UF vendedor > UF cliente).
    dias=None usa todo o histórico; dias=90 usa só as entregas dos últimos 90 dias antes do corte."""
    ref = treino if dias is None else treino[treino.ent >= t0 - pd.Timedelta(days=dias)]
    min_ref = 30 if dias is None else 20

    def med(base, chave, minimo):
        g = base.groupby(chave).dias_real.agg(["median", "size"])
        return g.loc[g["size"] >= minimo, "median"]

    return (df.rota.map(med(ref, "rota", min_ref))
              .fillna(df.rota.map(med(treino, "rota", 30)))
              .fillna(df.customer_state.map(med(ref, "customer_state", min_ref)))
              .fillna(df.customer_state.map(med(treino, "customer_state", 30)))
              .fillna(ref.dias_real.median()))


def preparar_fold(d, mes):
    """Treino = pedidos comprados E entregues antes do mês de teste. Teste = pedidos do mês."""
    t0 = pd.Timestamp(mes + "-01")
    t1 = t0 + pd.offsets.MonthBegin(1)
    tr = d[(d.ts < t0) & (d.ent < t0)].copy()
    te = d[(d.ts >= t0) & (d.ts < t1)].copy()
    for df in (tr, te):
        for sufixo, dias in (("h", None), ("r", 90)):
            t = tempo_tipico(tr, df, t0, dias)
            df[f"tipico_{sufixo}"] = t
            df[f"folga_{sufixo}"] = df.prazo_prometido - t
            df[f"folga_pct_{sufixo}"] = df.prazo_prometido / t
    return tr, te


# ----------------------------------------------------------------------------------
# 3. Modelos candidatos
# ----------------------------------------------------------------------------------
PEDIDO = ["n_itens", "n_sellers", "preco", "frete", "frete_pct", "peso_g", "vol_cm3", "peso_max",
          "dist_km", "mesmo_estado", "prazo_prometido", "dow", "hora"]
EPISODICAS = ["pedidos_7d", "seller_14d"]
FOLGA_H = ["prazo_prometido", "tipico_h", "folga_h", "folga_pct_h"]
FOLGA_R = ["prazo_prometido", "tipico_r", "folga_r", "folga_pct_r"]
CATEGORICAS = ["customer_state", "seller_state", "cat"]
FINAL = "Logística: folga da rota (últimos 90 dias)"


class TaxaPorUF:
    """Baseline: risco = taxa histórica de atraso do estado do cliente."""
    def fit(self, X, y):
        self.m_ = y.groupby(X["customer_state"].astype(str)).mean()
        self.g_ = y.mean()
        return self
    def predict_proba(self, X):
        p = X["customer_state"].astype(str).map(self.m_).fillna(self.g_).values
        return np.c_[1 - p, p]


def _logit(C=1.0):
    return make_pipeline(SimpleImputer(), StandardScaler(), LogisticRegression(max_iter=1000, C=C))


def _hgb():
    return HistGradientBoostingClassifier(categorical_features="from_dtype", random_state=0, max_iter=200,
                                          learning_rate=0.05, max_depth=4, min_samples_leaf=100,
                                          l2_regularization=5)


def catalogo_modelos():
    """nome -> (colunas, usa_categoricas, fábrica)"""
    return {
        "Baseline: taxa histórica por UF":          (["customer_state"], True, TaxaPorUF),
        "Logística: só prazo prometido":            (["prazo_prometido"], False, _logit),
        "Logística: folga da rota (histórico)":     (FOLGA_H, False, _logit),
        FINAL:                                      (FOLGA_R, False, _logit),
        "Gradient boosting: atributos do pedido":   (PEDIDO, True, _hgb),
        "Gradient boosting: tudo":                  (PEDIDO + FOLGA_R + EPISODICAS, True, _hgb),
    }


def _matriz(df, cols, usa_cat, cats_dtype):
    x = df[cols].copy()
    if usa_cat:
        for c in (CATEGORICAS if cols != ["customer_state"] else ["customer_state"]):
            if c in x.columns or c in df.columns:
                x[c] = df[c].astype(str).astype(cats_dtype[c])
    return x


def captura_topk(y, p, k=0.10):
    """Fração dos atrasos reais que estão entre os k% pedidos de maior risco."""
    n = max(1, int(len(y) * k))
    return float(np.asarray(y)[np.argsort(-np.asarray(p))[:n]].sum() / np.sum(y))


def avaliar_janelas(d, meses, modelos=None):
    """Avaliação em janelas móveis. Retorna (métricas por mês/modelo, previsões do modelo final)."""
    modelos = modelos or catalogo_modelos()
    cats = {c: pd.api.types.CategoricalDtype(sorted(d[c].astype(str).unique())) for c in CATEGORICAS}
    linhas, previsoes = [], []
    for mes in meses:
        tr, te = preparar_fold(d, mes)
        for nome, (cols, usa_cat, fab) in modelos.items():
            Xtr, Xte = _matriz(tr, cols, usa_cat, cats), _matriz(te, cols, usa_cat, cats)
            p = fab().fit(Xtr, tr.y).predict_proba(Xte)[:, 1]
            linhas.append(dict(mes=mes, modelo=nome, n_treino=len(tr), n_teste=len(te), prevalencia=te.y.mean(),
                               pred_medio=p.mean(), auc=roc_auc_score(te.y, p),
                               ap=average_precision_score(te.y, p),
                               cap10=captura_topk(te.y, p, 0.10), cap20=captura_topk(te.y, p, 0.20),
                               prec10=te.y.values[np.argsort(-p)[:int(len(te) * .10)]].mean()))
            if nome == FINAL:
                previsoes.append(te[["order_id", "ym", "y", "prazo_prometido", "tipico_r", "folga_r", "customer_state"]]
                                 .assign(p=p))
    m = pd.DataFrame(linhas)
    m["lift_ap"] = m.ap / m.prevalencia
    m["lift10"] = m.prec10 / m.prevalencia
    return m, pd.concat(previsoes, ignore_index=True)
