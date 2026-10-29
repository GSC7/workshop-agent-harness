# ADR 0008 — Grounding: o LLM propõe recomendações estruturadas, o código valida e escreve as seções prescritivas

- **Status:** aceito
- **Data:** 2026-10-28
- **Issue:** #6 (grounding das recomendações)

## Contexto

A regra de ouro do produto diz: toda recomendação prescritiva nasce de um hit da coleção
`prescricao`, amarrada a uma `fonte` rastreável. Até a #5, isso dependia só do prompt. O redator
escrevia recomendações e histórico em texto livre, e nada verificava se a fonte citada existia,
se era uma prescrição ou se explicava aquele KPI.

## Decisão

- **Nó `recomendar`** entre `enriquecer` e `relatorio`, na topologia fixa.
  - O LLM forte devolve uma **lista estruturada** (`Recomendacao`: prioridade, kpi, dimensão,
    ação, justificativa e `fonte`), com `json_schema`.
  - Sem hit de `prescricao` no run, o LLM nem é chamado.
- **Validação determinística** (`agent/grounding.validar_recomendacoes`). Uma recomendação só passa
  se a `fonte` for:
  1. recuperada **neste run**;
  2. da coleção **`prescricao`** (diagnóstico não sustenta prescrição);
  3. sobre o **mesmo KPI e dimensão** (`para_kpi`/`para_dimensao`).

  O que falha é **descartado**. As válidas são reordenadas e renumeradas pela prioridade.
  - A comparação é tolerante a caixa, acento e separador ("loja física" casa `loja_fisica`). O
    KPI e a dimensão gravados são os **do dado**, não a redação do LLM. A fonte é limpa de
    colchetes, crase e espaços.
  - **Uma prescrição pode explicar mais de um KPI fraco** (ex.: N2, Eletrônicos × marketplace):
    cada fonte guarda a lista `alvos`. Duplicatas por (fonte, KPI, dimensão) ficam só com a de
    maior prioridade.
  - **O texto da ação descartada nunca vai ao estado nem ao redator.** O estado guarda só a
    contagem; o detalhe (motivo e fonte) vai num evento SSE `recomendacoes_descartadas` e no
    log. Essa foi a 1ª revisão: a ação inventada chegava ao relatório pelos avisos.
- **O código escreve as seções prescritivas:**
  - "Recomendações priorizadas" sai só das validadas, cada uma com `[fonte]`;
  - sem nenhuma válida, a seção declara "Não há recomendação fundamentada";
  - "O que já funcionou / não funcionou" sai direto do campo `resultado` das prescrições
    (positivo × nulo/negativo), sem o LLM.
- **O texto livre do LLM fica restrito a diagnóstico e causas.** O redator recebe **só fontes de
  diagnóstico**. Cada citação `[minio://…]` do texto é conferida: fonte inexistente ou
  prescrição citada fora da seção validada viram aviso e uma nota visível no relatório.
- A ausência de recomendação declara o **motivo real**: sem prescrição, nenhuma prescrição
  recuperada se aplica (o LLM não propôs nada), falha do recomendador ou tudo descartado.
- **Falha do recomendador degrada:** o relatório sai com aviso e com a ausência de recomendação
  declarada, sem inventar nada.
- **SSE para a UI (#8):**
  - `recomendacoes` traz as validadas, estruturadas, e sai **sempre** (vazio sem queda real);
  - `fontes_citadas` traz só o que o relatório exibe: recomendações, histórico com resultado e
    citações válidas.

## Consequências

- Smoke real (OpenAI): N1 e a pergunta vaga trouxeram recomendações estruturadas e válidas:
  - frete grátis (positivo, 2023 e 2024);
  - evitar o brinde genérico (nulo);
  - "compre online, retire na loja" para a loja física.

  O histórico saiu certo e não houve descarte nem citação inválida.
- A citação errada vira **sinalização**, não remoção: o texto já foi transmitido em streaming. A
  nota visível e o aviso deixam a falha auditável.
- **Pendência (#10):** as recomendações e os descartes ainda não são gravados em `harness.runs`;
  hoje vão só para o SSE e para o texto do relatório no MinIO. O golden/judge deve medir a taxa
  de descarte.
- O `relatorio_anterior` (2026-05) é uma prescrição sem `resultado`. Ele não entra no histórico,
  mas continua disponível para "não repetir recomendações" na #7.
- **Barreira contra clarificação indevida** (regressão herdada da #5, achada no smoke desta
  fatia): o gpt-4o-mini marcou `fora_do_escopo` em 3 de 3 runs para "Como melhorar minhas
  vendas no próximo mês?", que é o caso central do PRD, e o run caía na clarificação. Agora, se
  a pergunta tem vocabulário do domínio comercial (`parece_do_dominio`), o `fora_do_escopo` do
  LLM é anulado no código. Depois disso, 3 de 3 runs seguiram o fluxo completo.
  O filtro **prefere falso positivo a falso negativo**: "metas de vida" ou "meu site pessoal"
  deixam de ser clarificadas, mas nenhuma pergunta de vendas vira clarificação. Uma pergunta de
  fora do domínio que passa pelo filtro roda a análise e devolve números de vendas; é um custo
  aceito.
