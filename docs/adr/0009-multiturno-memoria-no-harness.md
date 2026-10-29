# ADR 0009 — Multi-turno com memória nos runs do harness, herança determinística e "não repetir" em código

- **Status:** aceito
- **Data:** 2026-10-28
- **Issue:** #7 (chat multi-turno)

## Contexto

O PRD pede perguntas de acompanhamento no mesmo chat ("e na região Sudeste?") sem repetir o
contexto, e que recomendações já feitas em relatórios anteriores não se repitam (RF-09). O
corpus tem um `relatorio_anterior` (2026-05, recompra no Sul). Ele diz que maio já recomendou
resolver a logística e reativar o frete grátis, e que o próximo relatório deve **cobrar
execução e medição**, não reapresentar a ação. Até a #6, a N1 recomendava "reativar o frete
grátis" como se fosse novidade.

## Decisão

- **`conversa_id` no `POST /chat`** (opcional). Sem ele, a conversa é nova e o id volta no
  evento `inicio`.
- **A memória da conversa são os runs dela no `harness.runs`** (migration 0004: `conversa_id` +
  `recomendacoes`, mais `sazonais`, `premissas` e `avisos` para a auditoria). Não há
  checkpointer do LangGraph:
  - cada turno já é um run completo e auditável;
  - um checkpointer duplicaria um estado que o harness já guarda e exigiria outro schema
    gravável.
  - O repositório lê **só os runs `ok` daquela conversa** (`WHERE conversa_id = :id`): é esse
    filtro que impede vazamento entre conversas.
  - A identidade do run vive num objeto por requisição, nunca no serviço compartilhado.
- **Herança determinística** (`agent/conversa.herdar`). O acompanhamento troca só o que cita:
  - KPI e período ficam com o anterior se a pergunta não citar outros;
  - o eixo novo substitui o anterior;
  - o valor anterior só é herdado se o eixo não mudou ("Sul" não é um canal);
  - com conversa em andamento, pergunta que extraiu algo nunca é tratada como fora do escopo;
  - **o que foi herdado fica registrado** (`Plano.herdados`, fora do schema do LLM), e as
    premissas dizem "herdado do turno anterior", não "citado". Essa foi a 1ª revisão.
- **Não repetir:**
  - **Nesta conversa, em código:** recomendação com a mesma (fonte, KPI, dimensão) de uma já
    entregue vira **retomada**.
  - **Relatório anterior do corpus:** ele **não vale como fonte** de recomendação nova
    (validação). O texto dele vai ao recomendador, que marca `retomada` no que ele já cobriu.
  - As retomadas saem numa seção própria, "Acompanhamento do que já foi recomendado: cobrar
    execução e medir", e não em "Recomendações priorizadas". Sem nenhuma ação nova, a seção de
    recomendações diz isso explicitamente (motivo `so_retomadas`).
- **SSE:** `recomendacoes` traz só as novas; `retomadas` é um evento à parte.

## Consequências

- Smoke real numa conversa de 3 turnos:
  1. Recompra no Sul: frete grátis como retomada, pelo relatório de maio; nova: evitar o brinde.
  2. "e no Nordeste?": herdou a recompra; o Nordeste está no padrão histórico, sem enriquecimento.
  3. "E agora, o que fazer no Sul?": nada novo; o frete segue como acompanhamento e o brinde não
     reaparece.

  Ficaram 3 runs na mesma conversa, com as recomendações gravadas.
- A marcação de retomada pelo relatório anterior é **julgamento do LLM**: o relatório é texto
  livre e não aponta fontes. A parte da conversa é determinística. O golden e o judge (#10)
  devem medir isso.
- **Limites aceitos (revisão):**
  - dois turnos **simultâneos** da mesma conversa leem o mesmo histórico e podem entregar a
    mesma ação como nova. Serializar por `conversa_id` quando a UI (#8) existir;
  - num run **cancelado** depois do evento `recomendacoes`, o usuário viu as ações, mas a
    memória (só runs `ok`) não as conta. Elas podem voltar como novas no turno seguinte;
  - a chave (fonte, KPI, dimensão) não detecta a **mesma ação sustentada por outra fonte**;
  - o LLM pode marcar `retomada` numa ação inédita. O grounding segue garantido, mas o "não
    repetir" do relatório anterior é soft até o judge da #10;
  - o SQL real de `turno_anterior` só é exercitado pelo smoke; os testes usam um fake com a
    mesma regra.
- A conversa não expira: o histórico cresce com os runs. Basta para o uso atual; um limite de
  turnos fica para quando a UI (#8) existir.
