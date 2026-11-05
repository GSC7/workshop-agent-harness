# metrics/ — rastro de entrega do time

Métricas de **execução** (o Claude Code implementou bem as issues?) — não confundir com evals de
produto (o agente responde bem?). O `/scorecard` lê esta pasta + `git`/`gh` e gera o
relatório para o time e stakeholders.

## `entregas.jsonl`

Um objeto JSON **por linha**, um por issue concluída. Gravado ao fechar a issue (ver a
[Definição de Pronto](../.claude/rules/definicao-de-pronto.md)). Guarda os sinais que o `git` não
enxerga (tentativas até o gate verde, veredito do revisor, critérios de aceite).

Schema de cada linha:

```json
{
  "issue": 23,
  "titulo": "streaming SSE no /chat",
  "data": "2026-06-24",
  "criterios_aceite": { "total": 4, "atendidos": 4 },
  "gate": { "resultado": "verde", "tentativas_ate_verde": 2 },
  "revisor": { "veredito": "aprovado", "bloqueantes": 0, "ressalvas": 1 },
  "intervencoes_humanas": 1,
  "intervencoes": ["usuário definiu MINIO_API_PASSWORD no .env"],
  "tentativas_confiavel": true,
  "intervencoes_confiavel": true,
  "commit": "5c0af2a"
}
```

- `veredito`: `aprovado` · `aprovado com ressalvas` · `bloqueado`.
- `gate.resultado`: `verde` · `vermelho`.
- `tentativas_ate_verde`: quantas vezes o gate (`/validar` ou o hook de `Stop`) **reprovou** depois
  que o código estava pronto para comitar, antes de passar. **Verde de primeira = 0.** Não contam o
  vermelho intencional do TDD (teste escrito antes da implementação) nem falhas do eval de
  produto (ADR 0011), que tem métrica própria. Alto = agente brigando com o padrão.
- `intervencoes_humanas`: quantas ações humanas foram necessárias antes do merge: editar o
  código, configurar segredos/senhas no `.env`, ou aprovar uma decisão HITL que o agente não podia
  tomar. Liste cada uma, curta, em `intervencoes`. O ok de merge rotineiro não conta.
- `tentativas_confiavel` / `intervencoes_confiavel`: `true` só quando o registro segue as
  definições acima. Os 17 registros até 2026-10-30 estão `false`: os primeiros usavam 0 para verde
  de primeira e os seguintes 1 para a mesma coisa, e configurações no `.env` foram contadas em
  umas entregas e não em outras. Não foram reescritos, porque o detalhe de cada rodada não é
  recuperável e reescrever seria inventar. O `/scorecard` ignora os campos não confiáveis e diz
  quantos ficaram de fora.

O que é lead time, reabertura, autonomia (autor do commit) e mudança pedida em PR **não** vai aqui
— o `/scorecard` deriva isso direto de `git log` e `gh`, que o agente não consegue forjar.
