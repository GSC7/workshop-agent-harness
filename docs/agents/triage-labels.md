# Labels de triagem

| Label | Significado |
|---|---|
| `needs-triage` | Recém-aberta; ainda não avaliada. |
| `needs-info` | Bloqueada aguardando informação de um humano. |
| `ready-for-agent` | Fatia AFK: critérios de aceite claros, um agente pode implementar e merjar sem humano. |
| `ready-for-human` | Fatia HITL: exige decisão ou revisão humana (arquitetura, limiar de eval, design). |
| `wontfix` | Não será feita. |

Uma issue tem **uma** label de triagem por vez. Ao bloquear por falta de informação, troque para
`needs-info` e comente o que falta.
