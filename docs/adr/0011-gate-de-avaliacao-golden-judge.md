# ADR 0011 — Gate de avaliação: golden contra o sistema real, métricas em código, judge com critérios fixos

- **Status:** aceito (limiares e critérios **aprovados pelo usuário** em 2026-10-29)
- **Data:** 2026-10-29
- **Issue:** #10 (gate de avaliação EDD; HITL)

## Contexto

O PRD pede um critério objetivo de "bom o suficiente" antes de subir o código. Vários ADRs
(0007, 0008, 0009) deixaram medições para este gate. A linha de base no sistema real mostrou 4 de
6 narrativas passando nas métricas determinísticas. N2 e N3 falham por **recorte temporal**
("fim de 2025", "em julho") e **dimensão composta**, que são escopo da #9.

## Decisão

- **O eval roda contra o sistema real** (`POST /chat` via nginx, `backend/evals/execucao.py`), não
  só contra o grafo. Assim mede a pilha inteira: SQL, busca, grounding e SSE. Comando separado do
  `/validar`, porque chama o LLM e exige a pilha no ar:
  `PYTHONPATH=backend uv run python -m evals` (a partir da raiz do repo). Sai com código 1 se
  reprovar e grava `eval-report.json` e `eval-report.md` (ignorados no git).
- **Métricas determinísticas por caso** (`scoring.py`, puro):
  - o run concluiu;
  - enriqueceu ou não conforme o golden;
  - premissas no topo;
  - **recall das `fontes_esperadas` ≥ 0,75**;
  - **0 distrator** citado;
  - nenhuma citação fora do permitido (avisos de grounding do ADR 0008);
  - em caso de clarificação: passou pelo nó `clarificar` e rodou 0 SQL.
- **LLM-judge com critérios fixos no golden** (`criterios`, aprovados pelo usuário). O judge
  recebe a lista numerada e devolve só `atendidos[]` na mesma ordem, mais `inventou_informacao`.
  O código casa as respostas com os critérios por posição e rejeita contagem diferente
  (`ErroJudge` reprova o caso). A **nota é calculada em código** (atendidos ÷ total, zerada se
  houve invenção), e o limiar é **≥ 0,70 por caso**. O modelo é `LLM_MODEL_JUDGE`, ou o forte
  (gpt-4o). O judge só roda onde as métricas determinísticas passaram, para não gastar LLM com
  caso já reprovado.
  - Na primeira versão, o judge decompunha a resposta esperada em critérios a cada execução. O
    veredito virava sozinho: N4 deu 0,75 e depois 0,50; N6 deu 0,50 e depois 1,00. Com critérios
    fixos, 3 de 3 execuções deram APROVADO, com nota 1,00 nos casos contabilizados.
- **Falha conhecida não bloqueia** (decisão do usuário). Um caso com `pendente: "#N — motivo"`
  aparece no relatório com o motivo, mas fica fora do veredito até a issue dona entregar.
  Pendentes da **#9**:
  - N2 e N3: recorte temporal e dimensão composta;
  - N4: dimensão composta, e o redator não recebe os KPIs saudáveis;
  - N6: recorte do "fim do ano".
- **Robustez do gate (revisão):**
  - **sem nenhum caso contabilizado, o gate não aprova** (`all([])` seria `True`);
  - `--sem-judge` sai com código **2** ("passou, mas sem judge não vale como gate");
  - falha do judge (timeout, rate limit, resposta inválida) reprova **o caso**, não derruba o
    runner;
  - cada caso tem **prazo total** (o timeout do httpx é por operação), e o erro do servidor vai
    para os motivos;
  - um **pendente que passou** vira alerta "remova o `pendente`";
  - os prefixos dos avisos de grounding são constantes do grafo, importadas pelo scoring;
  - campo desconhecido no golden é erro (`extra="forbid"`);
  - invenção, para o judge, é só **causa ou ação** sem base: números vêm do banco, e ele não os
    vê.
- **Golden ampliado** (decisão do usuário) com `c1-fora-do-escopo`, que espera clarificação, 0 SQL
  e nenhuma recomendação.

## Consequências

- O gate hoje aprova com N1, N5 e C1. É um gate honesto sobre o que o agente já deveria acertar,
  e mostra no relatório o que ainda falta.
- Quando a #9 entregar, ela **deve remover o `pendente`** dos casos que resolver. O gate passa a
  cobrá-los, e é assim que a #9 prova o que fez.
- O relatório JSON é auditável: traz caso, run, métricas e cada critério do judge.
- **Variação residual do judge:** em cerca de 1 de 6 execuções a C1 caiu para 0,67, porque o
  judge leu "propor melhorias", no texto fixo de clarificação, como recomendação. O texto do
  agente foi reescrito sem ambiguidade (o critério aprovado não mudou), e depois disso 2 de 2
  execuções deram 1,00. Se a oscilação voltar, a próxima alavanca é a maioria de 3 julgamentos
  por caso.
- Custo: cerca de 7 runs do agente e até 7 chamadas do judge por execução.
- Limiares e critérios são decisão humana (HITL): mudar qualquer um exige atualizar este ADR.
