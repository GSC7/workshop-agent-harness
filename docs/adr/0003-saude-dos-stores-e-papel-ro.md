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
- **Dívida registrada (quitada na #26):**
  - o `env_file: .env` injetava no container da API segredos que ela não usa (`POSTGRES_PASSWORD`,
    `AGENTE_RO_PASSWORD`);
  - a API usava as credenciais root do MinIO.

  **Resolução (#26):**
  - **Sem `env_file`:** a api tem um `environment` explícito, só com o que o `Settings` lê em
    runtime. Os opcionais vêm como `${VAR:-}`, e o `Settings` usa `env_ignore_empty` (vazio =
    default do código).
  - **Teste de guarda:** `tests/test_compose_env.py` falha se entrar uma variável de admin
    (Postgres, MinIO root, segredos de admin do Langfuse) ou uma que o `Settings` não lê.
  - **Usuário restrito:** o serviço de execução única `minio-init` usa o root (só ali, na infra)
    para criar o bucket e o usuário `agente-relatorios`. A política `relatorios-rw`
    (`infra/minio/politica-relatorios.json`) dá só List/GetBucketLocation no bucket e
    Get/PutObject nos objetos; o `corpus` fica inacessível à api.
  - **Senha nova:** `MINIO_API_PASSWORD`, obrigatória.
  - **Host:** a api usa o par `MINIO_API_*` se completo; senão, o par `MINIO_ROOT_*` inteiro
    (`Settings.credenciais_minio`). Usuário e senha nunca são resolvidos separadamente: na 1ª
    versão, um `.env` com `MINIO_API_PASSWORD` e sem `MINIO_API_USER` misturava o root com a senha
    da api (achado da revisão). O seed segue com o root.
  - **Reexecução converge:** o `minio-init` roda a cada `up`; `mc admin user add` sobre usuário
    existente atualiza a senha, e `policy create` sobrescreve a política. Trocar a senha no `.env`
    e subir de novo basta.
  - **Pendente:** o root do MinIO ainda tem default `minioadmin` no compose (de antes da #26).
  - **Criação do bucket no startup:** continua como rede de segurança, mas no compose quem cria é
    o `minio-init` (a política não dá `CreateBucket` à api).
