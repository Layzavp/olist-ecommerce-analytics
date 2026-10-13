# Roteiro do Dashboard Power BI — Olist

Guia passo a passo do dashboard do projeto (modelo, medidas, 4 páginas e acabamento).

> **Validação:** as medidas DAX foram conferidas contra o cálculo equivalente em Python e, no Power BI Desktop, contra a tabela de **valores de conferência** (seção 6): os cartões do dashboard, sem filtros, batem com ela.

---

## 1. O que você tem em mãos

| Arquivo | Para quê |
|---|---|
| `dados/fato_pedidos.csv` | 96.211 pedidos (grão: 1 linha por pedido) |
| `dados/fato_itens.csv` | 109.880 itens (grão: 1 linha por item) |
| `dados/dim_produtos.csv` | produto → categoria (nomes em português) |
| `dados/dim_vendedores.csv` | vendedores (2.945) |
| `dados/dim_clientes.csv` | clientes (93.104) com a segmentação RFM |
| `dados/dim_uf.csv` | estados, regiões e país (para mapa e agrupamentos) |
| `tema_olist.json` | tema de cores e tipografia |
| `../exportar_powerbi.py` | regenera os CSVs a partir do `olist.db` |

**Escopo dos dados:** pedidos entregues, compra entre jan/2017 e ago/2018 (o mesmo do notebook, então os números batem).

---

## 2. Importação dos dados

1. **Página Inicial → Obter dados → Texto/CSV**, uma vez para cada arquivo em `dados/`.
2. Em cada janela de pré-visualização: **Origem do arquivo = 65001: Unicode (UTF-8)**, delimitador **vírgula** → **Transformar Dados**.

### Armadilha nº 1: separador decimal (leia isto!)
Os CSVs usam **ponto** como decimal (`129.90`). Se o seu Windows/Power BI está em português do Brasil, ele pode ler `129.90` como `12990`, **inflando a receita em 100x sem avisar**.

**Correção (faça antes de carregar):** em **Arquivo → Opções → Carregamento de Dados → Configurações regionais → Localidade para importação**, escolha **Inglês (Estados Unidos)**. Ou, no Power Query, por coluna: *Transformar → Tipo de Dados → Usando Localidade… → Inglês (EUA)*.

Depois de carregar, confira: `SUM(fato_itens[price])` deve ser **R$ 13.181.027,13**.

### Tipos de dados no Power Query
| Tabela | Coluna | Tipo |
|---|---|---|
| fato_pedidos | `data_compra` | Data |
| fato_pedidos | `dias_entrega`, `dias_atraso`, `receita_itens`, `frete_total` | Número decimal |
| fato_pedidos | `flag_atrasado`, `review_score`, `qtd_itens`, `prazo_estimado_dias` | Número inteiro |
| fato_itens | `price`, `freight_value` | Número decimal |
| dim_clientes | `monetario` | Número decimal |
| dim_clientes | `frequencia`, `recencia_dias`, `R`, `M` | Número inteiro |

Os demais campos são texto. **Não é preciso nenhuma outra transformação**: os CSVs já saem limpos do script.

---

## 3. Modelo de dados (esquema estrela)

### 3.1 Relacionamentos (Exibição de Modelo)

| De (lado "muitos") | Para (lado "um") | Cardinalidade | Direção do filtro |
|---|---|---|---|
| `fato_pedidos[data_compra]` | `dim_calendario[Date]` | Muitos → 1 | Única |
| `fato_pedidos[uf]` | `dim_uf[uf]` | Muitos → 1 | Única |
| `fato_pedidos[customer_unique_id]` | `dim_clientes[customer_unique_id]` | Muitos → 1 | Única |
| `fato_itens[order_id]` | `fato_pedidos[order_id]` | Muitos → 1 | Única |
| `fato_itens[product_id]` | `dim_produtos[product_id]` | Muitos → 1 | Única |
| `fato_itens[seller_id]` | `dim_vendedores[seller_id]` | Muitos → 1 | Única |

Mantenha **todas** as direções como **Única** (evite filtro bidirecional).

```
                dim_calendario      dim_uf       dim_clientes
                       \              |             /
                        \             |            /
                         ▼            ▼           ▼
dim_produtos ─────▶  fato_itens ◀───── fato_pedidos
dim_vendedores ───▶  (itens)    (order_id)  (pedidos)
```

### 3.2 Consequência que você precisa entender
O filtro flui de `fato_pedidos` **para** `fato_itens`, e nunca o contrário. Então:

- Filtros de **data, UF e cliente** chegam aos itens ✅ (receita por mês, por UF e por segmento funcionam).
- Filtros de **categoria e vendedor** (lado dos itens) **não chegam** em `fato_pedidos` ⚠️. Medidas calculadas sobre `fato_pedidos` (nota média, % atraso, entrega média) **não respondem** a um slicer de categoria.

Por isso: **receita e pedidos usam `fato_itens`**; logística e satisfação usam `fato_pedidos` e **não devem ficar em páginas com slicer de categoria**. Essa limitação está registrada na seção 8.

### 3.3 Tabela de calendário (DAX)
**Modelagem → Nova tabela:**

```dax
dim_calendario =
ADDCOLUMNS (
    CALENDAR ( DATE ( 2017, 1, 1 ), DATE ( 2018, 8, 31 ) ),
    "Ano", YEAR ( [Date] ),
    "Mês Nº", MONTH ( [Date] ),
    "Mês", FORMAT ( [Date], "mmm" ),
    "Ano-Mês", FORMAT ( [Date], "yyyy-mm" )
)
```

Depois: **Marcar como tabela de datas** (coluna `Date`) e, na coluna `Mês`, **Classificar por coluna → `Mês Nº`**.

### 3.4 Boas práticas do modelo
- Crie uma tabela vazia **`_Medidas`** (*Inserir dados* → tabela sem linhas) e guarde todas as medidas nela, em **pastas de exibição** (Vendas, Logística, Satisfação, Clientes, Vendedores).
- **Oculte** as colunas-chave (`order_id`, `product_id`, `seller_id`, `customer_unique_id`) e as colunas numéricas brutas depois de criar as medidas.
- Em `fato_pedidos[review_score]`, `[flag_atrasado]` e `dim_clientes[R]`/`[M]`: **Resumir por → Nenhum**.
- Formate `Receita` como **R$** e os percentuais com 1 casa decimal.

---

## 4. Medidas DAX

### Vendas  *(pasta: Vendas)*
```dax
Receita = SUM ( fato_itens[price] )

Pedidos = DISTINCTCOUNT ( fato_itens[order_id] )

Receita por Pedido = DIVIDE ( [Receita], [Pedidos] )   -- ticket médio

Receita por Item = DIVIDE ( [Receita], SUM ( fato_itens[order_item_id] ) )

Frete Total = SUM ( fato_itens[freight_value] )

Frete % Receita = DIVIDE ( [Frete Total], [Receita] )

Receita Ano Anterior =
CALCULATE ( [Receita], SAMEPERIODLASTYEAR ( dim_calendario[Date] ) )

-- Crescimento comparável: mesmos oito meses (jan–ago) de cada ano.
-- Ignora de propósito os filtros de calendário (ALL) para não quebrar com o slicer de Ano;
-- mantém os demais filtros (região, UF). Rotule o cartão como "Crescimento jan–ago/2018 vs 2017".
Receita YoY % =
VAR r18 =
    CALCULATE (
        [Receita],
        FILTER ( ALL ( dim_calendario ),
            dim_calendario[Date] >= DATE ( 2018, 1, 1 ) && dim_calendario[Date] <= DATE ( 2018, 8, 31 ) )
    )
VAR r17 =
    CALCULATE (
        [Receita],
        FILTER ( ALL ( dim_calendario ),
            dim_calendario[Date] >= DATE ( 2017, 1, 1 ) && dim_calendario[Date] <= DATE ( 2017, 8, 31 ) )
    )
RETURN
    DIVIDE ( r18 - r17, r17 )
```

### Logística  *(pasta: Logística — base: `fato_pedidos`)*
```dax
Entrega Média (dias) = AVERAGE ( fato_pedidos[dias_entrega] )

Prazo Estimado Médio (dias) = AVERAGE ( fato_pedidos[prazo_estimado_dias] )

% Atraso = AVERAGE ( fato_pedidos[flag_atrasado] )

Atraso Médio = AVERAGE ( fato_pedidos[dias_atraso] )   -- negativo = entrega antes do prazo
```

### Satisfação  *(pasta: Satisfação — base: `fato_pedidos`)*
```dax
Nota Média = AVERAGE ( fato_pedidos[review_score] )

Avaliações = COUNT ( fato_pedidos[review_score] )

% Notas Baixas =
DIVIDE (
    CALCULATE ( COUNTROWS ( fato_pedidos ), fato_pedidos[review_score] IN { 1, 2 } ),
    [Avaliações]
)

% Notas Baixas Vindas de Atrasos =
DIVIDE (
    CALCULATE (
        COUNTROWS ( fato_pedidos ),
        fato_pedidos[review_score] IN { 1, 2 },
        fato_pedidos[flag_atrasado] = 1
    ),
    CALCULATE ( COUNTROWS ( fato_pedidos ), fato_pedidos[review_score] IN { 1, 2 } )
)
```

### Clientes  *(pasta: Clientes)*
```dax
Clientes = DISTINCTCOUNT ( fato_pedidos[customer_unique_id] )

Clientes Recorrentes =
CALCULATE ( COUNTROWS ( dim_clientes ), dim_clientes[frequencia] >= 2 )

Taxa de Recompra % =
DIVIDE (
    CALCULATE ( COUNTROWS ( dim_clientes ), dim_clientes[frequencia] >= 2 ),
    COUNTROWS ( dim_clientes )
)

% Clientes =
DIVIDE ( COUNTROWS ( dim_clientes ), CALCULATE ( COUNTROWS ( dim_clientes ), ALLSELECTED () ) )

% Receita =
DIVIDE ( [Receita], CALCULATE ( [Receita], ALLSELECTED () ) )
```

### Vendedores  *(pasta: Vendedores)*
```dax
Vendedores Ativos = DISTINCTCOUNT ( fato_itens[seller_id] )

Receita Top 20% Vendedores % =
VAR n = ROUNDUP ( [Vendedores Ativos] * 0.2, 0 )
VAR topVend =
    TOPN ( n, ALLSELECTED ( dim_vendedores[seller_id] ), [Receita], DESC )
RETURN
    DIVIDE (
        CALCULATE ( [Receita], topVend ),
        CALCULATE ( [Receita], ALLSELECTED ( dim_vendedores ) )
    )

Receita Acumulada % =
VAR rec = [Receita]
VAR acima =
    FILTER ( ALL ( dim_vendedores ), [Receita] >= rec )
RETURN
    DIVIDE (
        SUMX ( acima, [Receita] ),
        CALCULATE ( [Receita], ALL ( dim_vendedores ) )
    )
```

### Título dinâmico (opcional)
```dax
Título Atraso =
"Pedidos atrasados geram " & FORMAT ( [% Notas Baixas Vindas de Atrasos], "0%" )
    & " das notas baixas, com apenas " & FORMAT ( [% Atraso], "0.0%" ) & " dos pedidos"
```
Use em **Formato → Título → fx (formatação condicional)**.

---

## 5. Páginas do dashboard

**Tela:** 16:9, 1920×1080. **Tema:** *Exibição → Temas → Procurar temas → `tema_olist.json`*.
**Grade:** margem de 24 px, espaço de 16 px entre visuais. Alinhe tudo (Formato → Alinhar/Distribuir).
**Ordenação:** gráficos com eixo de tempo (Ano-Mês) são ordenados **pelo próprio eixo, em ordem crescente**, nunca pela medida; categorias de atraso (`faixa_atraso`) também, pelo prefixo numérico. Ordenar pela medida embaralha os meses e esconde picos como a Black Friday.
**Cores:** o dashboard usa as cores do tema (azul principal, âmbar na segunda série; veja a lista no item 7). Cada UF do gráfico de dispersão recebe uma cor da paleta, em ordem alfabética.
**Regra de ouro:** cada título de gráfico diz **a conclusão**, não só o conteúdo. Ex.: em vez de "% atraso por UF", use "Nordeste concentra os maiores atrasos".

### Página 1 — Visão Executiva
Pergunta: *como está o negócio?*

| Zona | Visual | Campos |
|---|---|---|
| Topo (6 cartões) | Receita, Receita YoY %, Receita por Pedido, Pedidos, % Atraso, Nota Média | medidas |
| Esquerda-cima | Linha: receita mensal | Eixo `dim_calendario[Ano-Mês]`, Valor `Receita` |
| Esquerda-baixo | Colunas: atraso médio mensal (valores negativos = entrega antes do prazo) | Eixo `dim_calendario[Ano-Mês]`, Valor `Atraso Médio` |
| Direita-cima | Colunas: receita por região | Eixo `dim_uf[regiao]`, Valor `Receita` |
| Direita-baixo | Barras horizontais: Top 10 categorias | Eixo `dim_produtos[categoria]` (filtro Top N = 10 por `Receita`), Valor `Receita` |
| Faixa lateral | Segmentações (lista): Ano, Região | `dim_calendario[Ano]`, `dim_uf[regiao]` |

Insight de título sugerido: *"Receita cresceu 141% em jan–ago/2018 puxada por volume, com ticket estável"*.

### Página 2 — Logística
Pergunta: *onde e quanto a entrega falha?* (**sem slicer de categoria**)

| Zona | Visual | Campos |
|---|---|---|
| Topo (4 cartões) | Entrega Média, Prazo Estimado Médio, % Atraso, Frete % Receita | medidas |
| Esquerda | Barras ordenadas: % atraso por UF (filtro de visual `Pedidos ≥ 200` **aplicado**; opcional: cor condicional vermelha > 12%, **não aplicada**) | Eixo `dim_uf[uf]`, Valor `% Atraso` |
| Direita-cima | Dispersão: prazo x frete | X `Entrega Média (dias)`, Y `Frete % Receita`, Tamanho `Pedidos`, Detalhes `dim_uf[uf]` |
| Direita-baixo | Colunas + linha: Entrega Média (colunas) vs Prazo Estimado Médio (linha) por mês | Eixo `dim_calendario[Ano-Mês]`, 2 medidas |

Insight de título: *"O prazo prometido é ~2x o real, mas AL e MA ainda atrasam em 1 de cada 5 pedidos"*.
**Drillthrough (bônus):** crie uma página oculta "Detalhe da UF" com `dim_uf[uf]` como campo de drillthrough.

### Página 3 — Satisfação
Pergunta: *o atraso derruba a nota?* (**sem slicer de categoria**)

| Zona | Visual | Campos |
|---|---|---|
| Topo (3 cartões) | Nota Média, % Notas Baixas, **% Notas Baixas Vindas de Atrasos** | medidas |
| Esquerda | Barras horizontais: nota média por faixa de atraso | Eixo `fato_pedidos[faixa_atraso]` (excluir "Sem data de entrega" no filtro do visual), Valor `Nota Média` |
| Direita | Colunas 100% empilhadas: distribuição das notas | Eixo `faixa_atraso`, Legenda `review_score`, Valor `Avaliações` |

Insight de título: use a medida `Título Atraso`.
*(Bônus: visual **Influenciadores-chave** analisando "nota baixa" por UF, categoria de atraso e prazo.)*

### Página 4 — Clientes e Vendedores
Pergunta: *quem compra e quem vende?*

| Zona | Visual | Campos |
|---|---|---|
| Topo (3 cartões) | Clientes, Receita Top 20% Vendedores %, Taxa de Recompra % | medidas |
| Esquerda | Barras agrupadas: % clientes x % receita por segmento | Eixo `dim_clientes[segmento]`, Valores `% Clientes` e `% Receita` |
| Direita | Tabela Top 10 vendedores | `dim_vendedores[seller_id]` (Top N = 10 por `Receita`), `seller_state`, `Receita`, `Receita Acumulada %` |
| Canto inferior direito | Segmentação de data (intervalo) | `dim_calendario[Date]` |

**Interações obrigatórias (Formato → Editar interações):**
- A segmentação de **data não deve filtrar o gráfico de segmentos RFM** (configure a interação como *Nenhum* em Editar interações): a segmentação é uma fotografia do período inteiro, e `dim_clientes` não tem data. A segmentação de data continua afetando os cartões baseados em `fato_pedidos`/`fato_itens` e a tabela de vendedores.
- Ordene `segmento` manualmente: Novos de alto valor → Inativos de alto valor → Intermediários → Inativos → Novos → Recorrentes (ou por `% Receita`).

### Navegação
Adicione **botões de página** (*Inserir → Botões → Navegador → Navegador de páginas*) numa faixa no topo e **favoritos** para limpar filtros. Isso dá cara de produto.

---

## 6. Valores de conferência (valide TODAS as medidas)

Com **nenhum filtro** aplicado, o dashboard deve mostrar:

| Medida | Valor esperado |
|---|---|
| Receita | R$ 13.181.027 |
| Pedidos | 96.211 |
| Receita por Pedido (ticket médio) | R$ 137,00 |
| Frete % Receita | 16,6% |
| Entrega Média (dias) | 12,5 |
| % Atraso | 8,1% |
| Nota Média | 4,16 |
| % Notas Baixas | 12,8% |
| % Notas Baixas Vindas de Atrasos | 33,9% |
| Clientes | 93.104 |
| Taxa de Recompra % | 3,0% |
| Vendedores Ativos | 2.945 |
| Receita Top 20% Vendedores % | 82,2% |
| Receita YoY % (jan–ago/2018 vs jan–ago/2017) | +141,1% |

**Por que alguns números diferem do notebook:** o ticket do notebook (R$ 138,09) era a *média dos tickets mensais*; aqui é receita ÷ pedidos (R$ 137,00). Já o 33,8% (notebook) vs 33,9% (Power BI) vem de regras de atraso ligeiramente diferentes: o notebook usa `dias_atraso > 0` após arredondar a uma casa decimal; a medida usa `flag_atrasado`, baseada na comparação exata entre data/hora real e estimada. O notebook 02 também trabalha com 96.203 pedidos com data de entrega, contra 96.211 no conjunto total. Detalhes no README.

Testes extras de filtro:
- Filtre **UF = AL**: % Atraso ≈ 24,0%, Entrega Média ≈ 24,5 dias.
- Filtre **faixa_atraso = "4. Atraso > 7 dias"**: Nota Média ≈ 1,73.

---

## 7. Acabamento e publicação

**Checklist de qualidade**
- [ ] Todos os visuais com título que comunica a conclusão
- [ ] Eixos com unidade (R$, dias, %) e sem rótulos poluídos
- [ ] Sem gráfico de pizza; barras ordenadas; no máximo 4 cores por página
- [ ] Cores do tema `Olist Portfolio`, na ordem da paleta: azul `#2563eb` (principal), âmbar `#f59e0b`, vermelho `#dc2626`, cinza-azulado `#94a3b8`, verde-água `#0d9488`. No gráfico de notas: 5 = azul, 4 = âmbar, 3 = vermelho, 2 = cinza-azulado, 1 = verde-água
- [ ] Texto alternativo nos visuais (acessibilidade)
- [ ] Todos os números conferidos na seção 6
- [ ] Página final (ou caixa de texto) com **fonte dos dados, escopo e limitações**

**Para o portfólio**
1. Salve o arquivo de trabalho local como `olist_dashboard.pbix`. **Não publique o `.pbix` com dados embutidos por padrão**: os dados derivados têm licença própria e o arquivo pode conter os dados importados. Se quiser compartilhar o modelo, use **Arquivo → Exportar → Modelo do Power BI (`.pbit`)** e confira o conteúdo antes de publicar.
2. Depois de finalizar e validar todas as correções, exporte **PDF** (*Arquivo → Exportar → PDF*) e **prints PNG** das 4 páginas para `powerbi/prints/`. Os prints devem ser reexportados a partir da versão final do dashboard.
3. Grave um **GIF curto** (10–15 s) mostrando a interatividade (clicar numa UF, trocar o ano) e coloque no topo do README.
4. Se tiver conta e autorização para isso, publique no Power BI Service. Use **Publicar na web** somente após verificar as permissões e a licença dos dados: o link torna o relatório acessível publicamente. Se não for possível publicar, prints atualizados e PDF são suficientes para apresentar o resultado no portfólio.
5. Atualize o README principal com a seção "Dashboard", o GIF e a lista de páginas.

**Estrutura sugerida no repositório**
```
powerbi/
├── olist_dashboard.pbit       # opcional: modelo sem dados importados
├── tema_olist.json
├── roteiro_powerbi.md
├── prints/            # PNGs das 4 páginas + GIF
└── dados/             # CSVs (adicione ao .gitignore; o script regenera)
```

---

## 8. Limitações para declarar no projeto
- **RFM é uma fotografia do período inteiro**, sem corte por data.
- **Slicers de categoria/vendedor não afetam métricas de pedido** (nota, atraso), por causa da direção dos filtros do modelo.
- **Associação não é causalidade:** atraso e nota baixa andam juntos, mas outros problemas também pesam na nota.
- **Sem custo/margem:** receita não é lucro.
