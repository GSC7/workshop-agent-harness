"""Perna quantitativa: SQL templatizado de KPI x meta por eixo (ADR 0006).

Nenhum valor vindo do LLM entra no SQL: o plano escolhe (kpi, eixo) de um catálogo fechado e o
recorte por valor (ex.: "Sul") é aplicado depois, sobre o resultado.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, get_args

from agent.composto import Composto
from agent.periodo import Recorte

Kpi = Literal["faturamento", "ticket_medio", "taxa_recompra", "taxa_conversao"]
Eixo = Literal["total", "regiao", "canal", "categoria"]
KPIS: tuple[str, ...] = get_args(Kpi)

# Só os pares com meta cadastrada em negocio.metas (sem meta não há "abaixo da meta").
EIXOS_COM_META: dict[str, tuple[str, ...]] = {
    "faturamento": ("total", "regiao", "canal", "categoria"),
    "ticket_medio": ("total", "canal"),
    "taxa_recompra": ("total", "regiao"),
    "taxa_conversao": ("total", "canal"),
}

# eixo -> (coluna em pedidos/produtos, tabela da dimensão, coluna em metas)
_EIXO_SQL: dict[str, tuple[str, str, str]] = {
    "regiao": ("p.regiao_id", "regioes", "regiao_id"),
    "canal": ("p.canal_id", "canais", "canal_id"),
    "categoria": ("pr.categoria_id", "categorias", "categoria_id"),
}
_COLUNAS_META = ("regiao_id", "canal_id", "categoria_id")


def pares(kpis: list[str] | None, eixo: str | None) -> list[tuple[str, str]]:
    """(kpi, eixo) a consultar; pergunta vaga varre todo o catálogo com meta."""
    escolhidos = [k for k in (kpis or list(KPIS)) if k in EIXOS_COM_META]
    out: list[tuple[str, str]] = []
    for k in escolhidos:
        eixos = EIXOS_COM_META[k]
        out += [(k, eixo)] if eixo in eixos else [(k, e) for e in eixos]
    return out


def _base(kpi: str, eixo: str, meses_do_ano: Sequence[int] = ()) -> str:
    dim = _EIXO_SQL[eixo][0] if eixo != "total" else "0"
    janela = f"p.data_pedido >= lim.ini{_filtro_meses(meses_do_ano, 'p.data_pedido')}"
    if kpi == "faturamento" and eixo == "categoria":
        # Faturamento por categoria é a receita dos itens (camada_semantica / golden N2).
        return (
            f"SELECT {dim} AS dim_id, date_trunc('month', p.data_pedido)::date AS mes,"
            " sum(i.quantidade * i.preco_unitario) AS realizado"
            " FROM itens_pedido i JOIN pedidos p ON p.id = i.pedido_id AND p.status = 'pago'"
            f" JOIN produtos pr ON pr.id = i.produto_id CROSS JOIN lim WHERE {janela}"
            " GROUP BY 1, 2"
        )
    if kpi in ("faturamento", "ticket_medio"):
        valor = "sum(p.valor_total)" if kpi == "faturamento" else "sum(p.valor_total) / count(*)"
        return (
            f"SELECT {dim} AS dim_id, date_trunc('month', p.data_pedido)::date AS mes,"
            f" {valor} AS realizado FROM pedidos p CROSS JOIN lim"
            f" WHERE p.status = 'pago' AND {janela} GROUP BY 1, 2"
        )
    if kpi == "taxa_recompra":
        # Todo pedido conta; é recompra se o cliente já tinha pedido antes (golden N1).
        return (
            f"SELECT {dim} AS dim_id, date_trunc('month', p.data_pedido)::date AS mes,"
            " avg((EXISTS (SELECT 1 FROM pedidos a WHERE a.cliente_id = p.cliente_id"
            " AND a.data_pedido < p.data_pedido))::int) AS realizado"
            f" FROM pedidos p CROSS JOIN lim WHERE {janela} GROUP BY 1, 2"
        )
    # taxa_conversao: pedidos / sessões do mesmo canal x mês (golden N3).
    dim_s = "s.canal_id" if eixo == "canal" else "0"
    return (
        "SELECT a.dim_id, a.mes, a.ped::numeric / nullif(s.sess, 0) AS realizado FROM"
        f" (SELECT {dim} AS dim_id, date_trunc('month', p.data_pedido)::date AS mes,"
        f" count(*) AS ped FROM pedidos p CROSS JOIN lim WHERE {janela} GROUP BY 1, 2) a"
        f" JOIN (SELECT {dim_s} AS dim_id, date_trunc('month', s.data)::date AS mes,"
        " sum(s.sessoes) AS sess FROM sessoes_diarias s CROSS JOIN lim"
        f" WHERE s.data >= lim.ini{_filtro_meses(meses_do_ano, 's.data')} GROUP BY 1, 2) s"
        " ON s.dim_id = a.dim_id AND s.mes = a.mes"
    )


def _filtro_meses(meses_do_ano: Sequence[int], coluna: str) -> str:
    """Só meses válidos e inteiros entram no SQL (nada de texto de fora; bool não é mês)."""
    if not all(type(m) is int and 1 <= m <= 12 for m in meses_do_ano):
        raise ValueError(f"mês inválido: {meses_do_ano!r}")
    lista = ", ".join(str(m) for m in sorted(set(meses_do_ano)))
    return f" AND extract(month FROM {coluna}) IN ({lista})" if lista else ""


def sql_kpi(kpi: str, eixo: str, meses: int, meses_do_ano: Sequence[int] = ()) -> str:
    """Realizado x meta por valor do eixo nos últimos `meses` com dados (janela pelo dado).

    `meses_do_ano` restringe aos meses que a avaliação usa (#29): um recorte de julho não
    precisa dos outros 11 meses de cada ano (categoria × 60 meses chegava perto do teto). A
    coluna `dados_ate` é o último mês com pedidos, para a premissa não sair da série filtrada.
    """
    if eixo not in EIXOS_COM_META.get(kpi, ()):
        raise ValueError(f"sem meta para {kpi} por {eixo}")
    _filtro_meses(meses_do_ano, "p.data_pedido")  # valida cedo, antes de montar o SQL
    meta_dim = {c: "IS NULL" for c in _COLUNAS_META}
    if eixo != "total":
        _, tabela, col_meta = _EIXO_SQL[eixo]
        meta_dim[col_meta] = "= b.dim_id"
        nome, join_dim = "d.nome", f" JOIN {tabela} d ON d.id = b.dim_id"
    else:
        nome, join_dim = "'total'", ""
    cond_meta = " AND ".join(f"m.{c} {op}" for c, op in meta_dim.items())
    return (
        "WITH lim AS (SELECT (date_trunc('month', max(data_pedido))"
        f" - interval '{int(meses) - 1} months')::date AS ini,"
        " to_char(max(data_pedido), 'YYYY-MM') AS ultimo FROM pedidos),"
        f" b AS ({_base(kpi, eixo, meses_do_ano)})"
        f" SELECT {nome} AS dimensao, to_char(b.mes, 'YYYY-MM') AS mes,"
        " b.realizado, m.valor_meta AS meta, lim.ultimo AS dados_ate"
        f" FROM b{join_dim} CROSS JOIN lim"
        f" JOIN metas m ON m.kpi = '{kpi}' AND m.ano = extract(year FROM b.mes)"
        f" AND m.mes = extract(month FROM b.mes) AND {cond_meta}"
        " ORDER BY 1, 2"
    )


# risco_sazonal: mês-alvo citado historicamente abaixo da meta (#9, aprovado pelo usuário);
# padrao: recorte recorrente descrito (ex.: fim do ano vs resto do ano), sem prescrição.
Classificacao = Literal["queda_real", "sazonal", "saudavel", "risco_sazonal", "padrao"]


@dataclass(frozen=True)
class KpiAvaliado:
    kpi: str
    eixo: str
    dimensao: str
    gap_medio: float  # média de realizado/meta - 1 nos meses recentes (negativo = abaixo)
    # Recentes vs os mesmos meses dos 2 anos anteriores; None sem histórico comparável.
    variacao_yoy: float | None
    classificacao: Classificacao
    serie: list[dict[str, Any]]  # [{mes, realizado, meta}] recentes, p/ o relatório e a UI
    ano_referencia: int | None = None  # ano do recorte avaliado (#9)
    # Dimensão composta (#9b): sem meta própria (só ano a ano) e filtro do payload p/ a busca.
    sem_meta: bool = False
    filtro: dict[str, str] = field(default_factory=dict)
    # Recorte vs o resto do mesmo ano, por ano (ex.: fim do ano +11% em 2024).
    vs_resto_do_ano: dict[int, float] = field(default_factory=dict)

    @property
    def abaixo_da_meta(self) -> bool:
        return self.gap_medio < 0


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float, Decimal)) else 0.0


def _realizado(linha: dict[str, Any]) -> float | None:
    """NULL (ex.: nullif de sessões = 0) é ausência de dado, não realizado zero."""
    v = linha["realizado"]
    return float(v) if isinstance(v, (int, float, Decimal)) else None


def _mesmo_mes(mes: str, anos_atras: int) -> str:
    return f"{int(mes[:4]) - anos_atras:04d}{mes[4:]}"


def classificar(
    gap: float, yoy: float | None, limiar_meta: float, limiar_yoy: float
) -> Classificacao:
    """Abaixo da meta só é queda real se também cai contra o mesmo período dos anos anteriores.

    Abaixo da meta mas em linha com o histórico = sazonal (o padrão é o de sempre; a meta é que
    está alta). Sem histórico não se presume sazonalidade (ADR 0007).
    """
    if gap >= -limiar_meta:
        return "saudavel"
    if yoy is None or yoy < -limiar_yoy:
        return "queda_real"
    return "sazonal"


def avaliar(
    kpi: str,
    eixo: str,
    linhas: list[dict[str, Any]],
    meses_recentes: int,
    limiar_meta: float,
    limiar_yoy: float,
    meses_serie: int = 6,
) -> list[KpiAvaliado]:
    """Gap vs meta e variação vs anos anteriores, por valor do eixo; pior gap primeiro."""
    por_dim: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for linha in linhas:
        por_dim[str(linha["dimensao"])][str(linha["mes"])] = linha
    avaliados: list[KpiAvaliado] = []
    for dim, por_mes in por_dim.items():
        meses = sorted(por_mes)
        recentes = [
            m
            for m in meses[-meses_recentes:]
            if _num(por_mes[m]["meta"]) > 0 and _realizado(por_mes[m]) is not None
        ]
        if not recentes:
            continue
        real = {m: _realizado(por_mes[m]) or 0.0 for m in recentes}
        gap = sum(real[m] / _num(por_mes[m]["meta"]) - 1 for m in recentes) / len(recentes)
        # Mês a mês, só onde há histórico: nunca compara abr-jun com só abril de antes.
        razoes: list[float] = []
        for m in recentes:
            base = [
                r
                for anos in (1, 2)
                if (anterior := _mesmo_mes(m, anos)) in por_mes
                and (r := _realizado(por_mes[anterior])) is not None
                and r > 0
            ]
            if base:
                razoes.append(real[m] / (sum(base) / len(base)))
        yoy = sum(razoes) / len(razoes) - 1 if razoes else None
        avaliados.append(
            KpiAvaliado(
                kpi=kpi,
                eixo=eixo,
                dimensao=dim,
                gap_medio=gap,
                variacao_yoy=yoy,
                classificacao=classificar(gap, yoy, limiar_meta, limiar_yoy),
                serie=[
                    {
                        "mes": m,
                        "realizado": _realizado(por_mes[m]),
                        "meta": _num(por_mes[m]["meta"]),
                    }
                    for m in meses[-meses_serie:]
                ],
            )
        )
    return sorted(avaliados, key=lambda a: a.gap_medio)


def avaliar_recorte(
    kpi: str,
    eixo: str,
    linhas: list[dict[str, Any]],
    recorte: Recorte,
    limiar_meta: float,
    limiar_yoy: float,
) -> list[KpiAvaliado]:
    """Avalia o período CITADO (não os meses recentes): ano de referência = o do recorte, ou o
    último ano com o recorte completo; compara com o mesmo período 1-2 anos antes (#9)."""
    por_dim: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for linha in linhas:
        por_dim[str(linha["dimensao"])][str(linha["mes"])] = linha
    avaliados: list[KpiAvaliado] = []
    for dim, por_mes in por_dim.items():

        def mes(ano: int, m: int) -> str:
            return f"{ano:04d}-{m:02d}"

        def completo(ano: int) -> bool:
            return all(
                mes(ano, m) in por_mes and _realizado(por_mes[mes(ano, m)]) is not None
                for m in recorte.meses
            )

        anos = sorted({int(k[:4]) for k in por_mes if completo(int(k[:4]))})
        if not anos:
            continue
        ref = recorte.ano if recorte.ano in anos else (None if recorte.ano else anos[-1])
        if ref is None:
            continue
        # Série ancorada no ano pedido (nunca anos depois dele).
        anos_serie = [a for a in anos if a <= ref][-3:]
        # Mês-alvo é risco se for HISTORICAMENTE fraco: gap médio dos anos da série; nos demais,
        # o gap é o do ano de referência.
        anos_gap = anos_serie if recorte.tipo == "mes_alvo" else [ref]
        alvo = [mes(a, m) for a in anos_gap for m in recorte.meses]
        com_meta = [k for k in alvo if _num(por_mes[k]["meta"]) > 0]
        if not com_meta:
            continue  # sem meta não há "dentro da meta": pular, não declarar saudável
        gap = sum(
            (_realizado(por_mes[k]) or 0.0) / _num(por_mes[k]["meta"]) - 1 for k in com_meta
        ) / len(com_meta)

        def media(ano: int) -> float:
            return sum(_realizado(por_mes[mes(ano, m)]) or 0.0 for m in recorte.meses) / len(
                recorte.meses
            )

        base = [media(a) for a in (ref - 1, ref - 2) if a in anos]
        yoy = media(ref) / (sum(base) / len(base)) - 1 if base and sum(base) > 0 else None
        vs_resto: dict[int, float] = {}
        for a in anos:
            resto = [
                r
                for m in range(1, 13)
                if m not in recorte.meses
                and mes(a, m) in por_mes
                and (r := _realizado(por_mes[mes(a, m)])) is not None
            ]
            if resto and sum(resto) > 0:
                vs_resto[a] = media(a) / (sum(resto) / len(resto)) - 1
        if recorte.tipo == "padrao":
            classe: Classificacao = "padrao"
        elif recorte.tipo == "mes_alvo":
            classe = "risco_sazonal" if gap < -limiar_meta else "saudavel"
        else:
            classe = classificar(gap, yoy, limiar_meta, limiar_yoy)
        serie = [
            {
                "mes": mes(a, m),
                "realizado": _realizado(por_mes[mes(a, m)]),
                "meta": _num(por_mes[mes(a, m)]["meta"]),
            }
            for a in anos_serie
            for m in recorte.meses
        ]
        avaliados.append(
            KpiAvaliado(
                kpi=kpi,
                eixo=eixo,
                dimensao=dim,
                gap_medio=gap,
                variacao_yoy=yoy,
                classificacao=classe,
                serie=serie,
                ano_referencia=ref,
                vs_resto_do_ano=vs_resto,
            )
        )
    return sorted(avaliados, key=lambda a: a.gap_medio)


def sql_kpi_composto(composto: Composto, meses: int) -> str:
    """Faturamento (receita dos itens) por mês no recorte categoria × canal/região.

    Filtro por ids inteiros do catálogo do banco; o rótulo vem dos nomes no próprio banco.
    Não há meta composta em negocio.metas: a avaliação é só ano a ano.
    """
    col, tabela = (
        ("p.canal_id", "canais") if composto.eixo_b == "canal" else ("p.regiao_id", "regioes")
    )
    return (
        "WITH lim AS (SELECT (date_trunc('month', max(data_pedido))"
        f" - interval '{int(meses) - 1} months')::date AS ini FROM pedidos)"
        " SELECT c.nome || ' × ' || o.nome AS dimensao,"
        " to_char(date_trunc('month', p.data_pedido), 'YYYY-MM') AS mes,"
        " sum(i.quantidade * i.preco_unitario) AS realizado"
        " FROM itens_pedido i JOIN pedidos p ON p.id = i.pedido_id AND p.status = 'pago'"
        " JOIN produtos pr ON pr.id = i.produto_id JOIN categorias c ON c.id = pr.categoria_id"
        f" JOIN {tabela} o ON o.id = {col} CROSS JOIN lim"
        f" WHERE p.data_pedido >= lim.ini AND pr.categoria_id = {int(composto.categoria.id)}"
        f" AND {col} = {int(composto.outro.id)}"
        " GROUP BY 1, 2 ORDER BY 2"
    )


def avaliar_composto(
    eixo_b: str,
    filtro: dict[str, str],
    linhas: list[dict[str, Any]],
    recorte: Recorte | None,
    meses_recentes: int,
    limiar_yoy: float,
) -> list[KpiAvaliado]:
    """Sem meta composta: queda real = caiu além do limiar vs o mesmo período 1-2 anos antes."""
    por_mes = {str(ln["mes"]): _realizado(ln) for ln in linhas}
    validos = {m: r for m, r in por_mes.items() if r is not None}
    if not validos:
        return []
    if recorte is not None:
        anos = sorted({int(m[:4]) for m in validos})
        completos = [a for a in anos if all(f"{a:04d}-{mm:02d}" in validos for mm in recorte.meses)]
        ref: int | None
        if recorte.ano is not None:
            ref = recorte.ano if recorte.ano in completos else None
        else:
            ref = completos[-1] if completos else None
        if ref is None:
            return []
        alvo = [f"{ref:04d}-{mm:02d}" for mm in recorte.meses]
    else:
        alvo = sorted(validos)[-meses_recentes:]

    def mesmo(m: str, anos: int) -> str:
        return f"{int(m[:4]) - anos:04d}{m[4:]}"

    razoes = []
    for m in alvo:
        base = [validos[b] for a in (1, 2) if (b := mesmo(m, a)) in validos and validos[b] > 0]
        if base:
            razoes.append(validos[m] / (sum(base) / len(base)))
    if not razoes:
        return []  # sem histórico comparável não há como classificar sem meta
    yoy = sum(razoes) / len(razoes) - 1
    classe: Classificacao = "queda_real" if yoy < -limiar_yoy else "saudavel"
    serie_meses = sorted({x for m in alvo for x in (mesmo(m, 2), mesmo(m, 1), m) if x in validos})
    return [
        KpiAvaliado(
            kpi="faturamento",
            eixo=f"categoria×{eixo_b}",
            dimensao=str(linhas[0]["dimensao"]),
            gap_medio=yoy,  # sem meta: o desvio exposto é o ano a ano (sem_meta=True)
            variacao_yoy=yoy,
            classificacao=classe,
            serie=[{"mes": m, "realizado": validos[m], "meta": None} for m in serie_meses],
            ano_referencia=int(alvo[-1][:4]),
            sem_meta=True,
            filtro=dict(filtro),
        )
    ]
