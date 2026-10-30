"""LLMs dos nós, agnósticos de provedor (OpenAI ou Anthropic). Modelos só por settings."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent.grounding import ListaRecomendacoes, Recomendacao
from agent.state import Plano

_SISTEMA_PLANO = (
    "Você extrai a intenção de uma pergunta de um gestor comercial sobre vendas. "
    "KPIs possíveis: faturamento, ticket_medio, taxa_recompra, taxa_conversao. "
    "Eixos possíveis: regiao (Norte, Nordeste, Centro-Oeste, Sudeste, Sul), "
    "canal (site_proprio, marketplace, loja_fisica) e categoria de produto. "
    "Preencha só o que a pergunta cita; pergunta genérica ('minhas vendas') = kpis vazio. "
    "fora_do_escopo = true só se a pergunta não tiver relação com vendas ou indicadores comerciais "
    "(ex.: 'Como melhorar minhas vendas?' NÃO é fora do escopo)."
)

_SISTEMA_RELATORIO = """Você é um analista comercial. Escreva, em português do Brasil e em \
Markdown, a parte analítica de um relatório para o `periodo_alvo`, usando SOMENTE os dados do \
contexto JSON. Premissas, recomendações e histórico de tentativas são escritos à parte pelo \
sistema: NÃO os escreva.

Estrutura:
## Diagnóstico — cada item de `kpis_queda_real` com dimensão, `desvio_vs_meta_pct` e \
`variacao_vs_anos_anteriores_pct` exatos (não recalcule). Depois, numa subseção "Dentro do padrão \
histórico", cada item de `kpis_sazonais`: abaixo da meta mas igual ao mesmo período dos anos \
anteriores — sazonalidade ou meta alta, não queda real.
Se houver `kpis_saudaveis`, diga quais estão dentro ou acima da meta, com o \
`desvio_vs_meta_pct` e a `variacao_vs_anos_anteriores_pct` — oportunidade, não problema. Se houver \
`kpis_padrao_do_recorte`, descreva o padrão do período citado ano a ano pelo `vs_resto_do_ano_pct` \
(ex.: "sobe ~11% no fim do ano em 2023 e 2024; em 2025 não subiu"), sem tratá-lo como problema. \
Item com situação "risco para o período-alvo" é um período historicamente fraco que vai se \
repetir: diga isso e trate-o como algo a preparar.
## Causas prováveis — só para queda real, a partir das fontes de `colecao` diagnostico, citando \
cada uma entre colchetes com o campo `fonte` exato (ex.: [minio://corpus/diagnostico/...md]). \
Cite a fonte só para o KPI de `para_kpi`/`para_dimensao`. Sem fonte de diagnóstico, diga que a \
causa não está documentada.

Se o contexto trouxer `avisos`, declare-os numa linha logo no início (limitações do run).
Cada número pertence ao `kpi`, `eixo` e `dimensao` do seu item: NUNCA o atribua a um recorte \
mais fino citado na pergunta (ex.: a pergunta cita Eletrônicos no marketplace, mas o item é o \
marketplace inteiro — diga "o marketplace como um todo" e que o recorte combinado não foi medido).
Não invente números nem fontes. Sem nenhum KPI em queda real, diga que não há queda real a \
corrigir (citando os sazonais, se houver)."""

_SISTEMA_RECOMENDACAO = """Você propõe recomendações para KPIs em queda real, em português do \
Brasil, usando SOMENTE as fontes do contexto JSON.

Regras:
- Cada recomendação se apoia em UMA fonte com `colecao` = prescricao, cujo `para_kpi` e \
`para_dimensao` sejam o `kpi` e a `dimensao` da recomendação. Copie o campo `fonte` exato.
- Resultado positivo → repetir/adaptar a ação. Resultado nulo ou negativo → recomendar evitar.
- Priorize por impacto provável no KPI no `periodo_alvo` (1 = mais importante). Até 5 itens.
- Justificativa curta, com o que a fonte relata (números dela, se houver). Não invente.
- `relatorios_anteriores` traz o que já foi recomendado antes; `ja_recomendado_nesta_conversa`, o
que esta conversa já entregou. NÃO reapresente essas ações como novidade: se ainda forem
necessárias, proponha-as com `retomada` = true (o sistema as lista como "cobrar execução e
medir"). Prefira ações novas sustentadas por outras prescrições.
- Sem prescrição adequada, devolva a lista vazia."""


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


class RecomendadorLLM:
    """Modelo forte com saída estruturada; o grafo valida cada fonte antes de usar."""

    def __init__(self, modelo: BaseChatModel) -> None:
        self._llm = modelo.with_structured_output(ListaRecomendacoes, method="json_schema")

    async def recomendar(self, contexto: str) -> list[Recomendacao]:
        saida = await self._llm.ainvoke(
            [SystemMessage(_SISTEMA_RECOMENDACAO), HumanMessage(f"Contexto JSON:\n{contexto}")]
        )
        return cast(ListaRecomendacoes, saida).recomendacoes
