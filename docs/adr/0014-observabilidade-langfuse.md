# ADR 0014 — Observabilidade com Langfuse: stack próprio, trace por run, fora do caminho crítico

- **Status:** aceito (stack próprio, UI pela 2ª porta do nginx e init headless **escolhidos pelo
  usuário**)
- **Data:** 2026-10-29
- **Issue:** #11

## Contexto

O PRD pede um trace por run (nós, chamadas de LLM, `run_sql`, `search`) ligado ao registro do
run no schema `harness`. O Langfuse v4 não é um container só: precisa de web, worker, Postgres,
ClickHouse, Redis e um S3. Havia na máquina um Langfuse de outro projeto, que foi parado; este
projeto não depende dele.

## Decisão

- **Stack próprio no compose deste projeto** (`langfuse-web`, `langfuse-worker`,
  `langfuse-postgres`, `langfuse-clickhouse`, `langfuse-redis`, `langfuse-minio`), com volumes
  próprios. Os stores do agente (Postgres do `negocio`, MinIO do corpus) não são tocados: as
  migrações do Langfuse rodam só no banco dele.
- **Nenhum serviço novo publica porta.** A UI sai por um **2º `server` do nginx**
  (`LANGFUSE_PORT`, padrão 3000), porque o Langfuse não roda sob subcaminho (`/langfuse`) sem
  build próprio. A porta fica **só no loopback** (`127.0.0.1`): é a UI de admin. O login é por
  `http://localhost:<porta>` (`NEXTAUTH_URL`), e outro host ou IP quebra o callback. O MinIO do
  Langfuse usa só o endpoint interno.
- **Segredos obrigatórios e com prefixo `LANGFUSE_`:** `POSTGRES_*` e `MINIO_*` já são do workshop
  na interpolação do compose. Faltando um (ou vazio, como vem no `.env.example`), o compose recusa
  subir em vez de usar um default inseguro. A interpolação vale para o arquivo inteiro: **sem as
  `LANGFUSE_*`, nenhum comando do compose roda**, nem `up -d api` ou `ps`. Quem atualizar a branch
  precisa preencher o `.env` antes. Dentro do compose, então, o rastreamento sempre fica ligado; o
  "sem chaves = desligado" vale para a api rodando no host e para os testes. Um profile
  `observabilidade` tornaria o Langfuse opcional e fica como alternativa se isso incomodar. O
  projeto, as chaves da api e o usuário admin são criados no 1º boot (`LANGFUSE_INIT_*`).
- **O agente não conhece o Langfuse.** O `CallbackHandler` entra no `config` do `astream` e
  captura os nós e as chamadas de LLM. `run_sql` e `search` não são tools do LangChain (o nó as
  chama direto, grafo determinístico): `agent/rastro.como_tool` emite os callbacks de tool do
  LangChain com a config do nó corrente. Assim a tool fica **aninhada no nó, no mesmo trace**. Um
  span criado direto pelo SDK caía num trace separado (medido), porque o handler não define o
  contexto OTel corrente.
- **O id do trace é derivado do `run_id`** (`create_trace_id(seed=run_id)`) e gravado em
  `harness.runs.trace_id` (migração 0005). É o id **destinado**: se o export falhar (Langfuse fora),
  o registro aponta para um trace que não existe. `null` = sem chaves ou falha ao iniciar. Os
  turnos da mesma conversa formam uma sessão (`langfuse_session_id = conversa_id`).
- **Fora do caminho crítico:**
  - o export é assíncrono (OTel em lote, numa thread), e falha de export só gera log;
  - falha ao iniciar o trace faz o run seguir com `trace_id = null`;
  - sem as chaves, o rastreamento fica desligado;
  - a `api` não tem `depends_on` no Langfuse;
  - o encerramento descarrega o lote numa thread (`anyio.to_thread`), limitado pelo timeout do
    exporter (`LANGFUSE_TIMEOUT_S`, 5 s por tentativa), sem travar o event loop;
  - se o resumo de uma tool falhar ao registrar, o resultado é devolvido mesmo assim.

## Consequências

- Testes offline usam o SDK real com `span_exporter` em memória e `base_url` numa porta morta:
  provam nós, tools aninhadas, um trace só, a sessão e o id gravado. Um exporter **travado** prova
  que o `/chat` não espera o Langfuse (termina em menos de 3 s com o export pendurado). Os LLMs
  dos testes são fakes, então os spans de LLM só aparecem no smoke real.
- Custo: mais 6 containers. ClickHouse e o worker são os mais pesados.
- O `.env` passa a ter os segredos do Langfuse. Até a #26, a api os recebia via `env_file` sem
  usá-los. Desde a #26 (ADR 0003), ela recebe só `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
  `LANGFUSE_HOST` e `LANGFUSE_TIMEOUT_S`, e os segredos de admin ficam nos serviços do Langfuse.
- Na API pública do v4 (modo `events_only`), `/api/public/traces` não existe mais. Para
  inspecionar um run, use `/api/public/v2/observations?traceId=<trace_id>`.
