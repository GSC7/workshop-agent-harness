# Issue tracker

- **Onde:** GitHub Issues de `GSC7/workshop-agent-harness`, sempre via `gh` com `-R GSC7/workshop-agent-harness`
  (o remote `upstream` aponta para o repo original do workshop — **nunca** abra issue/PR lá).
- **Criar:** `gh issue create -R GSC7/workshop-agent-harness --title "..." --body-file corpo.md --label ready-for-agent`
- **Ler:** `gh issue view <n> -R GSC7/workshop-agent-harness --comments`
- **Listar prontas p/ agente:** `gh issue list -R GSC7/workshop-agent-harness --label ready-for-agent`

## Formato do corpo (saída do `/to-issues`)

`## O que construir` · `## Critérios de aceite` (checkboxes testáveis — ver Definição de Pronto) ·
`## Skills recomendadas` (skills de `.claude/skills/` a carregar antes de implementar) ·
`## Bloqueada por` (`#n` ou "Nenhuma").

## Fluxo

1. Pegue uma issue `ready-for-agent` cujos bloqueadores estejam fechados.
2. Carregue as **skills recomendadas** da issue.
3. Branch `feat/<n>-<slug>` → implementar → `/validar` → `revisor-codigo` → delivery record em
   `metrics/entregas.jsonl` → PR com `Closes #<n>`.
