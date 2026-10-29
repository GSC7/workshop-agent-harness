# ADR 0004 — Guardrails do `run_sql` por parser SQL (sqlglot), não por regex

- **Status:** aceito
- **Data:** 2026-10-28
- **Issue:** #2 (tool run_sql somente-leitura)

## Contexto

O `run_sql` recebe SQL escrito por LLM e precisa barrar, **antes do banco**, tudo que não for
leitura do schema `negocio`. Regex sobre o texto falha justamente nos casos que importam:
`;` dentro de string, comentário escondendo segunda instrução, DML dentro de CTE
(`WITH d AS (DELETE … RETURNING *) SELECT …`), `SELECT … INTO` (cria tabela) e `FOR UPDATE`.

## Decisão

- **Validar pela AST do `sqlglot`** (dialeto `postgres`). Nova dependência, pura Python.
  - Exatamente **uma** instrução; ilegível para o parser ⇒ rejeita (não entendeu, não executa).
  - Raiz só `SELECT` / operação de conjunto (`UNION`…); **nenhum nó** da árvore pode ser
    escrita, DDL, `GRANT`, comando opaco, `INTO` ou `Lock`.
  - Tabelas só do schema `negocio` (ou sem schema). Sem schema, `pg_*` é recusado, porque
    resolveria pelo `pg_catalog`, que está sempre no `search_path`.
  - **Funções em allowlist híbrida.** As que o `sqlglot` tipa (`Sum`, `Avg`, `Round`, `Lag`,
    `PercentileCont`…) são SQL padrão e passam. As desconhecidas (`Anonymous`) só passam se
    estiverem na allowlist explícita (`age`, `make_date`). É ali que moram as funções que
    executam SQL recebido como texto (`query_to_xml`, `table_to_xml`, `schema_to_xml`,
    `ts_stat`…). Elas escapariam da checagem de schema: o revisor apontou, e foi confirmado.
    Por cima vale uma denylist (`pg_*`, `lo_*`, `dblink*`, `set_config`), mesmo se o `sqlglot`
    passar a tipar alguma dessas. O `set_config` poderia desligar o read-only padrão de uma
    conexão que volta ao pool.
  - `LIMIT` garantido: injetado se ausente, rebaixado ao teto se maior, recusado se não for
    inteiro literal. O SQL que vai ao banco é **regerado da AST, sem comentários**.
- **Reforço no banco**, em toda execução: `SET TRANSACTION READ ONLY`,
  `SET LOCAL statement_timeout` e `SET LOCAL search_path = negocio`, sobre o papel `agente_ro`
  (ADR 0003). A execução usa `exec_driver_sql`, porque o `text()` do SQLAlchemy interpretaria
  `:nome` dentro de strings como bind param.
- Teto e timeout são parametrizáveis em `settings` (`sql_teto_linhas=500`,
  `sql_timeout_ms=5000`; defaults iniciais, a calibrar com o golden da #10).
- **Erro de banco vira mensagem de domínio** com o SQLSTATE, sem o SQL cru e sem detalhe do
  driver. A tool LangChain devolve `{"erro": …}` em vez de lançar, para o nó/LLM corrigir a
  consulta.

## Consequências

- Os 39 casos de guardrail são testados offline; o smoke real confirmou o timeout (57014), a
  tabela inexistente (42P01) e o `LIMIT` injetado.
- O `sqlglot` normaliza o SQL (maiúsculas, aliases com `AS`): o "SQL executado" exibido ao
  analista é o regerado, não o texto literal do LLM. É o que de fato rodou.
- Risco: o parser aceitar algo que o Postgres interpreta diferente. Mitigado pelas camadas no
  banco (papel RO + transação RO + timeout), que não dependem do parser.
- Custo da allowlist: uma consulta legítima com função desconhecida do `sqlglot` é recusada
  com "função não permitida". O agente reescreve a consulta, ou a função entra na allowlist
  depois de avaliada.
- **Pendência (#10):** o `LIMIT` limita o número de linhas, não o tamanho delas. Um
  `string_agg` sobre a tabela inteira devolve uma única linha enorme, contida só pelo timeout.
  Avaliar um teto de bytes na saída da tool quando o golden mostrar o tamanho real das
  respostas.
