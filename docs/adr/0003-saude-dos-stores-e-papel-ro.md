# ADR 0003 — Saúde dos stores (readiness 503 + liveness), papel `agente_ro` via Alembic e bucket no startup

- **Status:** aceito
- **Data:** 2026-10-28
- **Issue:** #1 (esqueleto andante)

## Contexto

O esqueleto precisa provar que a API alcança os três stores pré-populados e que o acesso ao
schema `negocio` é somente-leitura desde o primeiro dia. Três pontos admitiam mais de uma
resposta razoável:

1. Qual status HTTP o `/health` devolve com um store fora.
2. Onde nasce o papel `agente_ro` (o banco chega populado, mas sem o papel).
3. Quem cria o bucket `relatorios` do MinIO.

## Decisão

- **`/health` é readiness:** 200 com tudo ok, **503** com qualquer store degradado. O corpo é
  sempre JSON e, em erro, expõe só o *tipo* da exceção (nunca mensagem/stack, que podem conter
  host ou senha). O Postgres só conta como ok se a sessão for `agente_ro` **e** read-only.
- **`/health/live` é liveness** (sem I/O) e é o que o healthcheck do container usa. Com o
  healthcheck no `/health`, um store fora na partida a frio deixava a API `unhealthy` e o nginx
  (`depends_on: service_healthy`) nunca subia — o 503 não chegava a ninguém.
- **`agente_ro` nasce numa migration Alembic** (`infra/db/migrations`), rodada no host com a
  conexão admin (`DATABASE_URL`): `LOGIN`, `default_transaction_read_only = on`, `USAGE` +
  `SELECT` no `negocio` (inclusive tabelas futuras, via `ALTER DEFAULT PRIVILEGES`). Migration é
  infraestrutura, não ingestão: o `backend/` segue runtime puro e a API **só** conecta como
  `agente_ro` (`DATABASE_URL_RO`).
- **O bucket `relatorios` é criado no startup da API**, de forma idempotente; MinIO fora não
  derruba o startup (o `/health` reporta). É provisionamento de destino de escrita do próprio
  app, não carga de dados.

## Consequências

- Store fora é visível (503) sem tirar o app do ar; o compose sobe mesmo com store degradado.
- A defesa read-only existe no banco, independente dos guardrails da app (#2).
- **Dívida registrada:**
  - o `env_file: .env` injeta no container da API segredos que ela não usa (`POSTGRES_PASSWORD`,
    `AGENTE_RO_PASSWORD`);
  - a API usa as credenciais root do MinIO.

  Ideal: `env_file` enxuto + usuário MinIO com política restrita ao bucket `relatorios`. Tratar
  quando a escrita de relatórios entrar (#4).
