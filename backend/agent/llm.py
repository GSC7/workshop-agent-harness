"""LLMs dos nós, agnósticos de provedor (OpenAI ou Anthropic). Modelos só por settings."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent.state import Plano

_SISTEMA_PLANO = (
    "Você extrai a intenção de uma pergunta de um gestor comercial sobre vendas. "
    "KPIs possíveis: faturamento, ticket_medio, taxa_recompra, taxa_conversao. "
    "Eixos possíveis: regiao (Norte, Nordeste, Centro-Oeste, Sudeste, Sul), "
    "canal (site_proprio, marketplace, loja_fisica) e categoria de produto. "
    "Preencha só o que a pergunta cita; pergunta genérica ('minhas vendas') = kpis vazio."
)

_SISTEMA_RELATORIO = """Você é um analista comercial. Escreva, em português do Brasil e em \
Markdown, um relatório de melhoria para o próximo período usando SOMENTE os dados do contexto \
JSON (KPIs medidos neste run e fontes recuperadas).

Estrutura:
## Diagnóstico — TODOS os itens de `kpis_abaixo_da_meta` estão abaixo da meta: liste cada um \
com a dimensão e o `desvio_vs_meta_pct` exato (média dos meses recentes; não recalcule).
## Causas prováveis — a partir das fontes de diagnóstico.
## Recomendações priorizadas — cada uma termina com a fonte que a sustenta entre colchetes, \
copiando exatamente o campo `fonte` (ex.: [minio://corpus/prescricao/...md]).
## O que já funcionou e o que não funcionou — pelo campo `resultado` das prescrições.

Se o contexto trouxer `avisos`, declare-os numa linha logo no início (limitações do run).
Cada fonte traz `colecao` e `para_kpi`/`para_dimensao`: cite-a só para o KPI que ela explica.

Regras: não invente números nem fontes. Recomendação sem fonte no contexto não entra; se não \
houver prescrição com fonte, diga isso explicitamente. Se nenhum KPI estiver abaixo da meta, \
diga que os KPIs analisados estão saudáveis e não faça recomendações."""


Provedor = Literal["openai", "anthropic"]
Esforco = Literal["low", "medium", "high"]


def criar_modelo(
    provedor: Provedor,
    modelo: str,
    chave: SecretStr,
    max_tokens: int,
    timeout: float,
    esforco: Esforco | None = None,
) -> BaseChatModel:
    """Um ponto só sabe de provedor; nós e prompts não. Nenhum dos dois envia temperature."""
    if provedor == "anthropic":
        # Claude 5.x: thinking não desliga (effort controla) e tool forçada dá 400.
        return ChatAnthropic(
            model_name=modelo,
            api_key=chave,
            max_tokens_to_sample=max_tokens,
            timeout=timeout,
            stop=None,
            effort=esforco,
        )
    # reasoning_effort só vale p/ modelos de raciocínio; None (default) não é enviado.
    return ChatOpenAI(
        model=modelo,
        api_key=chave,
        max_completion_tokens=max_tokens,
        timeout=timeout,
        reasoning_effort=esforco,
    )


class PlanejadorLLM:
    """Modelo rápido: classificação curta com saída estruturada nativa (json_schema)."""

    def __init__(self, modelo: BaseChatModel) -> None:
        self._llm = modelo.with_structured_output(Plano, method="json_schema")

    async def planejar(self, pergunta: str) -> Plano:
        saida = await self._llm.ainvoke([SystemMessage(_SISTEMA_PLANO), HumanMessage(pergunta)])
        return cast(Plano, saida)


class RedatorLLM:
    """Modelo forte, em streaming: o relatório chega ao chat conforme é escrito."""

    def __init__(self, modelo: BaseChatModel) -> None:
        self._llm = modelo

    async def redigir(self, pergunta: str, contexto: str) -> AsyncIterator[str]:
        mensagens = [
            SystemMessage(_SISTEMA_RELATORIO),
            HumanMessage(f"Pergunta: {pergunta}\n\nContexto JSON:\n{contexto}"),
        ]
        async for pedaco in self._llm.astream(mensagens):
            if texto := pedaco.text:
                yield texto
