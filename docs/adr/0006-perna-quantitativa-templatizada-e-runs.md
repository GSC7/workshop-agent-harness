# ADR 0006 — Perna quantitativa com SQL templatizado, LLM só na intenção e no texto, runs por papel próprio

- **Status:** aceito
- **Data:** 2026-10-28
- **Issue:** #4 (caso central ponta a ponta)

## Contexto

O caso central ("como melhorar minhas vendas?") precisa dizer quais KPIs estão abaixo da meta e
em qual dimensão. Há duas formas de chegar ao número: o LLM gera o SQL, ou o código monta o SQL a
partir de um catálogo fechado. Além disso, o run precisa ser gravado no schema `harness`, mas o
único papel da API, o `agente_ro`, é somente-leitura **no banco**.

## Decisão

- **SQL templatizado** (`backend/agent/kpis.py`). Para cada par (KPI, eixo) com meta cadastrada:
  - faturamento: total, região, canal e categoria;
  - ticket médio: total e canal;
  - recompra: total e região;
  - conversão: total e canal.

  O código monta o SQL de realizado × meta por mês. As definições dos KPIs seguem a
  `camada_semantica` e o golden: recompra pelo `EXISTS` de um pedido anterior; conversão como
  pedidos ÷ sessões; faturamento por categoria pela receita dos itens.
  - **Nenhum valor vindo do LLM entra no SQL.** O recorte por valor (ex.: "Sul") é aplicado
    sobre o resultado, sem acento e sem diferenciar maiúsculas. Um valor desconhecido não zera
    a análise.
  - Os templates passam pelo mesmo `run_sql` (guardrails + transação RO + timeout), e cada um
    tem teste que prova isso.
  - A **janela vem do dado**: o último mês com pedidos, não `current_date`. Os dados terminam
    em 2026-06, e as premissas sobre período-alvo ficam para a #5.
- **"Fraco" = gap médio pior que −10%** nos 3 meses mais recentes, com no máximo 3 KPIs, o pior
  primeiro. Os valores são parametrizáveis em `settings`. As metas cadastradas são agressivas:
  quase toda dimensão fica entre −2% e −9%. Com o limiar, sobram as narrativas plantadas: Sul na
  recompra (−28%, N1) e loja física no faturamento (−23%, N5).
- **O LLM decide só dentro dos nós, numa camada agnóstica de provedor** (`agent/llm.py`,
  `criar_modelo`). A escolha é `LLM_PROVIDER=openai|anthropic`; o usuário optou pela OpenAI, que
  reaproveita a `OPENAI_API_KEY` dos embeddings.
  - `planejar`, com o modelo rápido, extrai KPI, eixo e valor do catálogo fechado. Usa saída
    estruturada `json_schema`, sem tool forçada (a tool forçada dá 400 nos Claude 5.x).
  - `relatorio`, com o modelo forte, redige em streaming só a partir do contexto do run.
  - **Modelos padrão = os do workshop original** (decisão do usuário): forte `gpt-4o`, rápido
    `gpt-4o-mini`, sobrescrevíveis por `LLM_MODEL_FORTE`/`LLM_MODEL_RAPIDO`. O PRD (§9) deixa
    a escolha de modelo para o harness e exige só a camada agnóstica de provedor. O esforço de
    raciocínio é opcional (`reasoning_effort`/`effort`).
  - Nenhum dos dois envia `temperature`, que a API da Anthropic rejeita desde 2026-10-21.
- **Topologia fixa** `planejar → perna_quantitativa → [enriquecer] → relatorio`. O único desvio
  é data-driven: sem KPI fraco, o run pula o enriquecimento.
- **Runs gravados por um papel `harness_rw`** (migration 0003), que só pode `INSERT` e `SELECT`
  em `harness.runs`. O `agente_ro` segue read-only no banco. O relatório vai para o MinIO
  (`minio://relatorios/<run_id>.md`), e a URI fica no run. Um run com erro também é gravado,
  só com o tipo da exceção.
- **Sem a chave do provedor, o `/chat` responde 503** e o resto do app sobe normalmente. Já a
  `HARNESS_RW_PASSWORD` é **obrigatória no compose** (`:?`): sem ela o `docker compose up`
  falha com mensagem clara, em vez de o app subir e falhar só na hora de gravar o run.
- **Run cancelado também é gravado.** Se o cliente desconecta no meio do SSE, o run vai para o
  harness com status `cancelado`. A gravação roda num `anyio.CancelScope(shield=True)`: o
  Starlette cancela pelo anyio, que reentrega o cancelamento a cada `await`, e `asyncio.shield`
  sozinho não esperaria a gravação (reproduzido em teste com gravação de 50 ms).
- **Falha de busca degrada, não aborta.** O run segue só com os números e registra um aviso.
  Avisos (busca indisponível, valor citado inexistente) vão ao redator, que os declara no topo.

## Consequências

- O número do relatório é reproduzível e auditável: o SQL executado vai para o SSE e para o run.
- Pergunta fora do catálogo de KPIs com meta não tem resposta quantitativa. A exploração ad-hoc
  do analista fica para a #9, que pode usar o `run_sql` com SQL gerado.
- Nova variável de ambiente: `HARNESS_RW_PASSWORD`.
- O grounding rigoroso (validação de fonte por recomendação e saída estruturada) fica para a #6.
  Na #4 o prompt pede citação, mas nada ainda verifica. Para reduzir atribuição errada, cada
  hit já carrega `colecao`, `para_kpi` e `para_dimensao`.
- A seção de premissas no topo do relatório (rules/agente.md) fica para a #5.
- As stubs do `langgraph` 1.2.6 rejeitam `add_node` no mypy, inclusive o exemplo canônico. Há um
  `type: ignore` pontual, com comentário.
