---
description: Gate rápido de validação — backend (ruff + mypy + pytest) e frontend (tsc + vite build + vitest). Rode antes de cada commit.
---

# /validar — gate rápido

Rode o gate de validação do time e **conserte até passar**. Este é o mesmo gate que o hook de
`Stop` aplica automaticamente; rode-o explicitamente antes de commitar uma fatia.

```bash
uv run ruff check --fix backend && uv run mypy backend && uv run pytest -q
npm --prefix frontend run build && npm --prefix frontend test
```

- Se **ruff** acusar: deixe o autofix resolver o que dá e corrija o resto à mão.
- Se **mypy** acusar: conserte a tipagem; não silencie com `# type: ignore` sem justificar.
- Se **pytest** falhar: conserte o código (não o teste, salvo se o teste estiver errado).
- Se o **build do frontend** falhar (tsc estrito): conserte a tipagem; nada de `any`.
- Se o **vitest** falhar: mesma regra do pytest.

Só considere a tarefa pronta quando o gate sair **verde**. Não comite vermelho.
