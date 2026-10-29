"""Perna quantitativa: SQL templatizado de KPI x meta por eixo (ADR 0006).

Nenhum valor vindo do LLM entra no SQL: o plano escolhe (kpi, eixo) de um catálogo fechado e o
recorte por valor (ex.: "Sul") é aplicado depois, sobre o resultado.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, get_args

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


def _base(kpi: str, eixo: str) -> str:
    dim = _EIXO_SQL[eixo][0] if eixo != "total" else "0"
    janela = "p.data_pedido >= lim.ini"
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
        " WHERE s.data >= lim.ini GROUP BY 1, 2) s ON s.dim_id = a.dim_id AND s.mes = a.mes"
    )


def sql_kpi(kpi: str, eixo: str, meses: int) -> str:
    """Realizado x meta por valor do eixo nos últimos `meses` com dados (janela pelo dado)."""
    if eixo not in EIXOS_COM_META.get(kpi, ()):
        raise ValueError(f"sem meta para {kpi} por {eixo}")
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
        f" - interval '{int(meses) - 1} months')::date AS ini FROM pedidos),"
        f" b AS ({_base(kpi, eixo)})"
        f" SELECT {nome} AS dimensao, to_char(b.mes, 'YYYY-MM') AS mes,"
        " b.realizado, m.valor_meta AS meta"
        f" FROM b{join_dim}"
        f" JOIN metas m ON m.kpi = '{kpi}' AND m.ano = extract(year FROM b.mes)"
        f" AND m.mes = extract(month FROM b.mes) AND {cond_meta}"
        " ORDER BY 1, 2"
    )


@dataclass(frozen=True)
class KpiAvaliado:
    kpi: str
    eixo: str
    dimensao: str
    gap_medio: float  # média de realizado/meta - 1 nos meses recentes (negativo = abaixo)
    serie: list[dict[str, Any]]  # [{mes, realizado, meta}] p/ o relatório e a UI

    @property
    def abaixo_da_meta(self) -> bool:
        return self.gap_medio < 0


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float, Decimal)) else 0.0


def avaliar(
    kpi: str, eixo: str, linhas: list[dict[str, Any]], meses_recentes: int
) -> list[KpiAvaliado]:
    """Gap médio realizado x meta nos últimos meses, por valor do eixo; pior primeiro."""
    por_dim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for linha in linhas:
        por_dim[str(linha["dimensao"])].append(linha)
    avaliados: list[KpiAvaliado] = []
    for dim, serie in por_dim.items():
        serie = sorted(serie, key=lambda r: str(r["mes"]))
        recentes = [r for r in serie[-meses_recentes:] if _num(r["meta"]) > 0]
        if not recentes:
            continue
        gaps = [_num(r["realizado"]) / _num(r["meta"]) - 1 for r in recentes]
        avaliados.append(
            KpiAvaliado(
                kpi=kpi,
                eixo=eixo,
                dimensao=dim,
                gap_medio=sum(gaps) / len(gaps),
                serie=[
                    {"mes": r["mes"], "realizado": _num(r["realizado"]), "meta": _num(r["meta"])}
                    for r in serie
                ],
            )
        )
    return sorted(avaliados, key=lambda a: a.gap_medio)
