# ADR 0007 — Premissas resolvidas em código; queda real × sazonal contra os mesmos meses dos anos anteriores

- **Status:** aceito
- **Data:** 2026-10-28
- **Issue:** #5 (premissas declaradas + saudável/fraco)

## Contexto

O PRD exige três coisas:
- resolver período e escopo sozinho quando a pergunta é vaga, **declarando as premissas no topo**;
- separar **queda real de variação sazonal**;
- devolver pergunta só quando nada é resolvível.

A #4 marcava como "fraco" todo desvio pior que −10% contra a meta. Nos dados reais isso misturava
casos diferentes. Brinquedos (−11,3% na meta) está **+3,6% acima** dos mesmos meses dos anos
anteriores: a meta é que ficou alta. Já Sul na recompra (−27,9%) e loja física (−22,7%) caem
também contra o próprio histórico (−23,1% e −22,2%).

## Decisão

- **Período-alvo em código** (`agent/periodo.py`): mês atual + 1, com virada de ano, ou a próxima
  ocorrência do mês citado. "Hoje" é injetável, para o período ser testável.
  - **O mês citado vem do texto da pergunta** (`mes_nomeado`, por regex com os nomes dos meses,
    com ou sem acento), não do LLM. No smoke real o LLM preencheu `mes_citado=11` para "próximo
    mês", e a premissa declarava a origem errada. Mais de um mês nomeado é ambíguo e cai no
    default.
- **Premissas escritas pelo código** no topo do relatório, antes do texto do LLM:
  - período-alvo e de onde ele veio;
  - escopo (KPIs e recorte);
  - **até quando há dados** (2026-06, distante do período-alvo 2026-11);
  - janela recente, comparação sazonal e critério de queda real.

  O relatório nunca sai sem elas, e o LLM é instruído a não repeti-las.
- **Classificação determinística** (`kpis.classificar`), comparando a média dos 3 meses recentes
  com os mesmos meses dos 2 anos anteriores:
  - **saudável:** desvio contra a meta ≥ −10%;
  - **queda real:** abaixo da meta **e** queda pior que −10% contra os anos anteriores. Sem
    histórico comparável, também conta como queda real: não se presume sazonalidade;
  - **sazonal:** abaixo da meta, mas dentro do padrão histórico.

  A comparação é feita **mês a mês, só nos meses que têm histórico** (nunca abr–jun de 2026
  contra só abril de antes), e um realizado NULL é descartado, não vira zero. A janela da
  consulta passa de 6 para 27 meses. Os limiares ficam em `settings`.
- **As premissas declaram o escopo que de fato rodou** (os pares KPI × eixo executados). Um eixo
  pedido sem meta vira aviso, e resultado no teto de linhas também.
- **Roteamento em código:**
  - só a queda real é enriquecida;
  - os sazonais vão ao relatório numa seção "Dentro do padrão histórico", sem recomendação;
  - tudo saudável leva a um relatório com premissas e "não há queda real", sem busca.
- **Clarificação:** o planejador marca `fora_do_escopo`. O código manda a pergunta para o nó
  `clarificar`, que devolve uma resposta fixa listando o que o agente analisa e terminando com
  pergunta. Não roda SQL nem chama o redator.

## Consequências

- Smoke real: a pergunta vaga devolve queda real em Sul/recompra e loja física, as narrativas
  N1/N5, e coloca Brinquedos e Nordeste/recompra como "dentro do padrão". A N6 (ticket médio)
  sai saudável e sem enriquecimento, como o golden espera. "Em julho" leva a 2027-07. Uma
  pergunta fora do domínio leva à clarificação.
- **Limitação, a tratar na #9:** a análise compara os meses **recentes**, não o mês-alvo citado.
  Na N3 ("conversão do site em julho"), o golden espera o recorte sazonal de julho (queda todo
  inverno) e as prescrições do site. Isso é a consulta sazonal comparativa do analista.
- **Limitação do `mes_nomeado`:** qualquer mês nomeado vira alvo, inclusive em contexto de
  passado ("desde janeiro", "comparado a julho passado"), e "marco" sem acento casa o
  substantivo. O mês corrente citado vai para a próxima ocorrência ("fechar outubro" dito em
  outubro dá o próximo outubro), o que a premissa declara. Refinar com a intenção temporal
  na #9.
- A recompra no Nordeste (−11% na meta, −4,6% no ano a ano) fica na fronteira: é classificada
  como sazonal. A calibração dos limiares vai para o golden e o judge da #10.
