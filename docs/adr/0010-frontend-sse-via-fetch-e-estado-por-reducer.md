# ADR 0010 — Frontend: SSE via `fetch`, estado do turno por reducer puro, rastro inspecionável

- **Status:** aceito
- **Data:** 2026-10-29
- **Issue:** #8 (frontend React + Vite)

## Contexto

A UI precisa mostrar o relatório enquanto ele é escrito, expor fontes e SQL ao analista, tratar
os estados de carregando, erro, vazio e sucesso, e falar só com o nginx, sem segredo no bundle. O
backend emite cerca de 15 tipos de evento SSE no `POST /chat`, inclusive `conversa_id` para o
multi-turno.

## Decisão

- **SSE consumido via `fetch` + `ReadableStream`** (`src/api/chat.ts`), não via `EventSource`,
  que não faz `POST`. O parser é incremental (`src/api/sse.ts`), porque um evento pode chegar
  partido entre pedaços da rede. JSON inválido é erro de protocolo visível, nunca descarte
  silencioso.
- **Contrato tipado** como união discriminada por `tipo` (`src/api/tipos.ts`), espelhando o
  backend, sem `any`.
- **Estado do turno por reducer puro** (`src/estado/turno.ts`): cada evento gera um novo turno.
  É testável sem DOM (vitest), e um stream que termina sem `fim`/`erro` vira erro explícito: a
  tela nunca fica "carregando" para sempre.
- **Markdown com `react-markdown`**, que não renderiza HTML cru: o texto do LLM não injeta nada
  na página. Só o `cursor` de escrita é decorativo.
- **Estados com componente próprio** (`Estados.tsx`):
  - *Carregando:* etapas do grafo e um esqueleto do relatório, só enquanto não há texto;
  - *Erro:* mensagem por causa (503, 422, rede, stream interrompido) e "Tentar de novo";
  - *Vazio:* explica o que o assistente faz e oferece perguntas de exemplo.
- **Rastro inspecionável**, recolhido por padrão em `<details>`: recomendações estruturadas
  (novas e em acompanhamento, mais a contagem de descartadas), fontes citadas e SQL executado.
- **Acessibilidade:** tokens semânticos claro/escuro desenhados juntos, foco visível, alvos de
  44px, `aria-busy` no turno em streaming, e só a conclusão anunciada via `aria-live` (anunciar
  cada pedaço seria inutilizável), link "ir para a pergunta" e `prefers-reduced-motion`.
- **Mesma origem:** o nginx serve a SPA, num build multi-stage, e faz o proxy de
  `/chat`/`/health`. Em dev, o Vite faz proxy para o nginx.

## Consequências

- Smoke do **cliente real** (`api/chat.ts` + parser + reducer) contra o backend via nginx:
  - 266 pedaços de relatório entre 5,7 s e 7,9 s;
  - o 2º turno reaproveitou o `conversa_id` devolvido no `inicio` e herdou o KPI;
  - o bundle não tem chave nem endereço de serviço interno (o único "sk-" é `mask-type`/`task-`
    do React).
- **Não verificado visualmente nesta sessão** (sem navegador). Layout, tema escuro e 375px
  ficaram verificados só por leitura de código; falta uma conferência humana.
- O gate do `/validar` cobre só o backend. O frontend tem `npm run build` (tsc estrito) e
  `npm test` (vitest), mas eles ainda não entram no gate. Incluí-los é uma mudança de harness, a
  decidir.
- **Robustez (revisão):** evento de tipo desconhecido é ignorado pelo reducer; um `LimiteDeErro`
  evita tela branca em erro de render. Ele é a **única classe** do app (no React, error boundary
  só existe como classe), uma exceção consciente à regra "sem class components". O rastro
  aparece também em erro/cancelamento, se já houve SQL ou fontes. "Tentar de novo" só aparece
  para erro recuperável. A rolagem automática só acompanha quem está no fim e respeita reduced
  motion. O fim do stream processa o que sobrou no buffer.
- Fontes `minio://…` aparecem como texto, porque não há endpoint para abrir o documento. Um
  visualizador fica para depois.
