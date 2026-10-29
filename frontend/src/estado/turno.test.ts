import { describe, expect, it } from "vitest";
import { aplicarEvento, novoTurno } from "./turno";

const rec = {
  prioridade: 1, kpi: "taxa_recompra", dimensao: "Sul", acao: "Frete grátis",
  justificativa: "Funcionou.", fonte: "minio://corpus/prescricao/frete.md", retomada: false,
};

describe("reducer do turno", () => {
  it("começa enviando e passa a transmitir no início", () => {
    const t = aplicarEvento(novoTurno("Como melhorar?"), { tipo: "inicio", run_id: "r1", conversa_id: "c1" });

    expect(t.status).toBe("transmitindo");
    expect(t.runId).toBe("r1");
  });

  it("acumula o relatório pedaço a pedaço", () => {
    let t = novoTurno("x");
    t = aplicarEvento(t, { tipo: "relatorio", texto: "## Diag" });
    t = aplicarEvento(t, { tipo: "relatorio", texto: "nóstico" });

    expect(t.relatorio).toBe("## Diagnóstico");
  });

  it("guarda o rastro: etapas, SQL, fontes, recomendações, retomadas e avisos", () => {
    let t = novoTurno("x");
    t = aplicarEvento(t, { tipo: "etapa", nome: "perna_quantitativa" });
    t = aplicarEvento(t, { tipo: "sql", kpi: "taxa_recompra", eixo: "regiao", sql: "SELECT 1" });
    t = aplicarEvento(t, { tipo: "fontes_citadas", fontes: [rec.fonte] });
    t = aplicarEvento(t, { tipo: "recomendacoes", recomendacoes: [rec] });
    t = aplicarEvento(t, { tipo: "retomadas", retomadas: [{ ...rec, retomada: true }] });
    t = aplicarEvento(t, { tipo: "recomendacoes_descartadas", itens: [{ motivo: "m", fonte: "f" }] });
    t = aplicarEvento(t, { tipo: "aviso", mensagem: "busca indisponível" });

    expect(t.etapa).toBe("perna_quantitativa");
    expect(t.sqls).toEqual([{ kpi: "taxa_recompra", eixo: "regiao", sql: "SELECT 1" }]);
    expect(t.fontesCitadas).toEqual([rec.fonte]);
    expect(t.recomendacoes).toHaveLength(1);
    expect(t.retomadas).toHaveLength(1);
    expect(t.descartadas).toBe(1);
    expect(t.avisos).toEqual(["busca indisponível"]);
  });

  it("avisos repetidos não se acumulam", () => {
    let t = novoTurno("x");
    t = aplicarEvento(t, { tipo: "aviso", mensagem: "a" });
    t = aplicarEvento(t, { tipo: "aviso", mensagem: "a" });

    expect(t.avisos).toEqual(["a"]);
  });

  it("fim conclui com a URI do relatório; erro guarda a mensagem do servidor", () => {
    const fim = aplicarEvento(novoTurno("x"), { tipo: "fim", run_id: "r", relatorio_uri: "minio://relatorios/r.md" });
    const erro = aplicarEvento(novoTurno("x"), { tipo: "erro", run_id: "r", mensagem: "tente de novo" });

    expect([fim.status, fim.relatorioUri]).toEqual(["concluido", "minio://relatorios/r.md"]);
    expect([erro.status, erro.erro]).toEqual(["erro", "tente de novo"]);
  });

  it("não muta o turno anterior", () => {
    const t = novoTurno("x");
    aplicarEvento(t, { tipo: "relatorio", texto: "a" });

    expect(t.relatorio).toBe("");
  });
});

describe("robustez do reducer", () => {
  it("evento de tipo desconhecido não derruba a tela", () => {
    const t = novoTurno("x");
    const desconhecido = { tipo: "evento_novo_do_backend" } as unknown as Parameters<typeof aplicarEvento>[1];

    expect(aplicarEvento(t, desconhecido)).toBe(t);
  });
});
