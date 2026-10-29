# ADR 0005 — Filtro da `search` por coleção, com a dimensão em AND exato

- **Status:** aceito (decisão do usuário em 2026-10-28)
- **Data:** 2026-10-28
- **Issue:** #3 (tool search)
- **Complementa:** ADR 0002 (`periodo_referencia` opcional)

## Contexto

O invariante dizia: *enriquecimento Qdrant sempre filtrado por `kpi_alvo` + dimensão*. A
inspeção dos 32 pontos reais mostrou que esse texto não se aplica igualmente às coleções:

| Coleção | `kpi_alvo` | Dimensão (`regiao`/`produto`/`canal`) |
|---|---|---|
| `camada_semantica` | em 6 de 7 (a de `tabela` não tem) | nunca |
| `diagnostico` | **nunca** (0 de 10) | sempre ao menos uma |
| `prescricao` | sempre | às vezes; há docs globais, sem dimensão |

Ao pé da letra, `diagnostico` e `camada_semantica` voltariam sempre vazias, e o *porquê*
sumiria do relatório.

Também vale registrar que `ano` e `mes` estão indexados como `integer`, mas o payload guarda
strings (`'2024'`, `'6'`). Um filtro inteiro nesses campos pode não casar.

## Decisão

- **`prescricao`:** `kpi_alvo` + dimensão obrigatórios.
- **`diagnostico`:** dimensão obrigatória. O `kpi_alvo` é ignorado, porque o campo não existe
  no dado.
- **`camada_semantica`:** é catálogo do schema, não enriquecimento. `kpi_alvo` é opcional e não
  há dimensão.
- **A dimensão casa em AND exato** sobre os campos preenchidos. Docs globais, sem dimensão,
  **não** entram.
- **Recorte sazonal** por `periodo_referencia` (`MatchAny` de `AAAA-MM`). `ano` e `mes` ficam
  fora do filtro, por causa da inconsistência de tipo.
- **Nunca `data_ingestao`.** Os campos do filtro são fixos (`Dimensao`, `kpi_alvo`,
  `periodos`), sem chave livre vinda do LLM, e um teste verifica as três coleções.
- O filtro é validado **antes** de gastar embedding. Hit sem `fonte` é descartado, porque não
  pode ser citado.

## Consequências

- Smoke real (embedding OpenAI + Qdrant): **recall de 100%** das `fontes_esperadas` do golden
  em N1, N2, N3 e N5, sem nenhum distrator. O único extra (N1) é o `relatorio_anterior` de
  2026-05, insumo de "não repetir recomendações" (#7).
- Os docs globais (Black Friday, Dia das Mães) ficam de fora de buscas com dimensão. Se uma
  pergunta exigir o geral, o nó precisa buscar sem recorte numa coleção que permita isso.
  Hoje não há caso no golden.
- O texto do invariante foi atualizado para esta regra em `CLAUDE.md`,
  `.claude/rules/agente.md`, `.claude/rules/testes.md`, `.claude/rules/definicao-de-pronto.md`
  e no `revisor-codigo`. O ADR 0002 foi marcado como parcialmente substituído.
- String vazia ou só com espaços em `kpi_alvo` ou na dimensão conta como ausente: um
  `regiao=""` vindo do LLM é rejeitado, em vez de zerar a busca em silêncio.
- `qdrant-client>=1.20`, para casar com o servidor 1.20 dos stores.
