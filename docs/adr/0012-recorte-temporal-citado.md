# ADR 0012 — Recorte temporal citado na pergunta: mês-alvo como risco sazonal, padrão recorrente e período passado

- **Status:** aceito (regra de risco sazonal e entrega em duas partes **aprovadas pelo usuário**)
- **Data:** 2026-10-29
- **Issue:** #9, parte a (recorte temporal); a parte b é a dimensão composta

## Contexto

Até a #8, a análise comparava sempre os 3 meses mais recentes (abril a junho de 2026), qualquer
que fosse o período citado. O gate da #10 mostrou o efeito. A N3 ("conversão do site em julho") e
a N6 ("ticket sobe no fim do ano") falhavam, e a N2 ("caiu no fim de 2025") também. Medindo nos
dados:
- **julho:** a conversão do site fica em cerca de 0,020 contra meta de 0,030 em **todos** os anos
  (−33%, igual ao histórico);
- **fim do ano:** o ticket sobe 10–12% sobre janeiro a outubro em 2022–2024, mas no 4º tri de 2025
  não subiu e ficou −13% contra a meta e −10% contra 2024;
- **N2:** o 4º tri de 2025 de Eletrônicos × marketplace caiu −44% contra 2024.

## Decisão

- **O recorte é extraído do texto, em código** (`periodo.recorte_citado`). O LLM não decide isso.
  São três tipos:
  - **`mes_alvo`**, um mês sem ano ("em julho"): esse mês nos anos anteriores. Se ficou abaixo da
    meta, é **risco sazonal para o período-alvo** e é enriquecido (regra aprovada pelo usuário).
    O "sazonal" sem período citado continua sem prescrição.
  - **`padrao`**, "fim do ano" sem ano: o padrão recorrente, outubro a dezembro **contra o resto
    do ano**, ano a ano. É **descritivo e sem enriquecimento**. Se o 4º tri de 2025 fosse avaliado
    contra a meta, daria queda real e dispararia prescrição justamente no controle saudável da
    N6; a pergunta é sobre o padrão, não sobre 2025.
  - **`passado`**, período com ano ("fim de 2025", "4º tri de 2024", "julho de 2025"): aquele
    período contra o mesmo período 1 e 2 anos antes, com a classificação normal (queda real,
    sazonal ou saudável).
  - Dois meses ("maio e junho") são ambíguos e caem no default, sem escolha silenciosa.
  - "fim do ano de 2025" e "final do ano passado" (o ano é o de hoje − 1) são período passado.
    "Último trimestre" **não** vira outubro a dezembro: em pt-BR costuma ser o trimestre mais
    recente, então cai no default.
  - "Anteriores" só recorta a prescrição em "novembros" ou "novembro anteriores"; um "ano
    anterior" solto não conta.
  - Período passado ("julho de 2025") não vira mês-alvo no plano, nem é herdado depois.
  - **Mês em contexto de passado (#28):** cada menção de mês é classificada pelo contexto
    (`periodo._mencoes`). Só a menção sem marca de passado vira mês-alvo.
    - "julho **do ano passado**" é julho do ano anterior.
    - "julho **passado**" é o último julho que já passou: em outubro de 2026, julho de **2026**.
      A issue dizia "do ano anterior", mas em pt-BR "julho passado" dito em outubro é o deste
      ano. Se o mês ainda não chegou ou é o corrente ("novembro passado" ou "outubro passado" em
      outubro), é o do ano anterior. Sem dados do período, vale o aviso de recorte incompleto.
    - "**desde** janeiro" não vira mês-alvo nem recorte: é um intervalo até hoje, que o recorte
      não modela. Cai no default declarado nas premissas.
    - "**marco**" sem acento só é mês com contexto de data antes ("em/de/desde/até/para marco")
      ou depois ("marco de 2025"); "o marco do projeto" não é mês. "Março" com acento é sempre
      mês.
    - "comparado a julho passado, e agosto?" vira recorte passado (julho), e o agosto é
      descartado como mês-alvo pela regra de período passado acima. Do mesmo modo, "comparando
      julho com julho do ano passado" vira recorte passado (jul do ano anterior), não mês-alvo.
    - "Anteriores" (plural ou "X anteriores") é da **menção**: o substantivo "marcos" não recorta
      a prescrição de julho (achado da revisão).
    - **Limites:** a presença de "março" acentuado vale para a frase toda (libera todo "marco");
      "Marco foi bom?" e "nos marcos" caem no default (conservador); "julho do ano anterior" e
      "julho deste ano" ainda viram mês-alvo futuro; "desde julho de 2025" cai no default.
- **Avaliação no recorte** (`kpis.avaliar_recorte`):
  - **mês-alvo é risco se for historicamente fraco**: usa o gap médio dos anos da série, não um
    ano só;
  - a série é ancorada no ano pedido, sem anos posteriores; sem meta, a dimensão é pulada, em
    vez de declarada saudável;
  - período passado ganha uma janela que cobre 2 anos antes (até 60 meses), como base do ano a
    ano;
  - o ano de referência é o citado, ou o último com o recorte **completo**;
  - a janela da consulta sobe para 39 meses quando há recorte;
  - **só os meses que a avaliação usa (#29):** em mês-alvo e período passado, a consulta filtra
    pelos meses do recorte (`extract(month ...) IN (...)`, só inteiros 1–12). Faturamento por
    categoria em "fim de 2023" (janela de 60 meses) caiu de **480 para 120 linhas** (teto de
    500), medido no banco. O **padrão** ("fim do ano") segue com todos os meses, porque compara com
    o resto do ano; por categoria, chega a 39 × 8 = 312 linhas. Com o filtro, o `vs_resto_do_ano`
    some do mês-alvo e do passado, onde o prompt não o usa. "Dados até" vem de uma coluna
    `dados_ate` do próprio SQL (último mês com pedidos), não da série filtrada. A recompra continua
    olhando todo o histórico do cliente: o filtro é do pedido avaliado. O recorte composto (#9b)
    tem uma dimensão só (no máximo 60 linhas) e fica sem filtro;
  - a série exibida é só a do recorte.
- **Enriquecimento no recorte:**
  - o **diagnóstico** é filtrado por `periodo_referencia` dos meses do recorte (o "porquê" é
    daquele período, nunca por `data_ingestao`);
  - a **prescrição** fica sem filtro de período, porque o que funcionou pode ser de qualquer época;
  - exceção: "X **anteriores**" ou mês no plural ("novembros anteriores") recorta também a
    prescrição.
- **O redator recebe os KPIs saudáveis** do recorte e os **padrões** (`vs_resto_do_ano_pct`), para
  afirmar "acima da meta" e "sobe ~11% no fim do ano" com número. O prompt proíbe atribuir um
  número a um recorte mais fino que o analisado.
- **As premissas declaram o recorte** e os meses de fato analisados. "Dados disponíveis até" vem
  do banco, não da série do recorte. A linha de sazonalidade diz qual comparação foi feita, e um
  recorte sem dados completos gera aviso.
- **Multi-turno:** um mês-alvo herdado do turno anterior leva junto o recorte, e a análise usa o
  mesmo período que a premissa declara (1ª revisão).

## Consequências

- Gate (ADR 0011): **N3 e N6 saem de `pendente` e passam a contar**. 3 de 3 execuções
  APROVADAS, com judge 1,00 nos dois casos.
- **N2 e N4 continuam pendentes, agora da #9b** (dimensão composta), pelo motivo certo:
  - **N2:** o resultado depende do eixo que o planejador escolhe. Com canal, a análise é do
    marketplace inteiro e o texto chegou a atribuir o número a Eletrônicos; com categoria, é
    Eletrônicos no Brasil e sai sazonal. A resposta certa precisa de Eletrônicos × marketplace.
  - **N4:** passa no gate, mas responde sobre Beleza no Brasil, não Beleza × Nordeste. O alerta
    "pendente que passou" dispara e **é mantido de propósito**: passar pelo motivo errado não
    resolve o caso.
- **Lição para o judge:** sem acesso aos números do banco, ele não detecta número atribuído ao
  recorte errado (aconteceu na N2). Fica como limite registrado da #10.
