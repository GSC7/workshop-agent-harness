# ADR 0013 — Dimensão composta (categoria × canal/região) detectada em código, avaliada pelo ano a ano

- **Status:** aceito
- **Data:** 2026-10-29
- **Issue:** #9, parte b; fecha a issue

## Contexto

A N2 ("Eletrônicos **no marketplace**") e a N4 ("Beleza **no Nordeste**") pedem um recorte que
cruza duas dimensões. O planejador escolhe **um** eixo, e o resultado dependia de qual: com canal
(marketplace inteiro), o texto chegou a atribuir o número a Eletrônicos; com categoria, saía
Eletrônicos no Brasil inteiro. A tabela `negocio.metas` **não tem meta composta**: há meta por
categoria e por canal/região, separadas.

## Decisão

- **Detecção em código** (`agent/composto.py`):
  - uma categoria e exatamente um canal **ou** uma região citados na pergunta formam o composto;
  - os nomes vêm do **catálogo do próprio banco** (`regioes`, `canais`, `categorias`),
    consultado uma vez por grafo. O grafo é compartilhado entre requisições: o cache é atribuído
    de uma vez, sem duplicar itens. Um item duplicado viraria "duas categorias" e desligaria o
    composto em silêncio (achado da revisão). Falha ou vazio não ficam em cache;
  - acompanhamento de um recorte composto ("e no fim de 2024?") continua composto: o detector
    também roda sobre o valor herdado;
  - a comparação é tolerante a acento e caixa, aceita "e" por "&" e a 1ª palavra ("casa"), e
    sempre por palavra inteira ("Norte" não casa "Nordeste");
  - ambiguidade (duas categorias, ou canal e região juntos) não vira composto.
- **SQL sem nenhum texto de fora** (`sql_kpi_composto`): filtra por **ids inteiros** do catálogo,
  e o rótulo `categoria × canal` vem dos nomes no banco. Passa pelo mesmo `run_sql`.
- **Só faturamento.** É o único KPI com categoria (receita dos itens). Composto só com outro
  KPI gera aviso e a análise segue pelo eixo simples. Se faturamento vier junto com outro KPI,
  os demais são declarados como não analisados.
- **Grounding:** a recomendação para a dimensão composta casa mesmo escrita como "x" ou "/".
  Só uma das partes ("marketplace") não basta.
- **Sem meta composta, a classificação é pelo ano a ano** (`avaliar_composto`): queda real se a
  queda for pior que −10% contra o mesmo período 1–2 anos antes; senão, sem queda. Funciona com o
  recorte temporal da 9a ("fim de 2025") e, sem recorte, com os 3 meses recentes. Sem histórico
  comparável, a dimensão não é classificada.
- **O recorte pedido substitui a varredura:** com composto, roda **só** ele. As premissas
  declaram "categoria×canal (Eletrônicos × marketplace)".
- **Enriquecimento pelas duas dimensões** em AND (`Dimensao(produto=…, canal=…)`, ADR 0005).
- **O redator é avisado de que não há meta:** `desvio_vs_meta_pct = null`,
  `sem_meta_composta = true`, e a situação diz "sem meta própria: avaliado só pelo ano a ano".
  Antes ele repetia "dentro ou acima da meta" para um recorte sem meta.

## Consequências

- **N2 e N4 deixam de ser pendentes.** O gate fica com **os 7 casos contando** e saiu APROVADO em 3
  de 3 execuções.
  - N2: Eletrônicos × marketplace, −40,7% no 4º tri/2025 contra a média de 2023–2024; as 3 fontes
    do golden citadas.
  - N4: Beleza × Nordeste, +3,4% em abril a junho contra os mesmos meses de 2024–2025 (não os
    +28% do semestre inteiro), sem enriquecimento.
- Com isso a **#9 fecha** (9a + 9b): KPI específico por dimensão, inclusive composta; recorte
  sazonal; comparação com anos anteriores e prescrições com fonte; fora do domínio recusado.
- Limites:
  - não há composto categoria × canal × região (três dimensões);
  - nome de categoria fora das variantes ("decoração" sozinho) não é detectado e cai no eixo
    simples;
  - catálogo indisponível gera aviso no log e a análise segue sem composto;
  - a variante "1ª palavra" ("casa", "esporte") pode dar falso positivo numa frase como "vendas
    para casa no Sul";
  - composto com mês-alvo futuro ("Eletrônicos no marketplace em julho") é classificado pelo
    ano a ano do último julho completo (queda real ou sem queda), não como risco sazonal como no
    eixo simples, porque sem meta não há "historicamente abaixo da meta".
