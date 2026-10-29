import { describe, expect, it } from "vitest";
import { ErroProtocolo, criarLeitorSse } from "./sse";

describe("leitor SSE", () => {
  it("lê eventos completos", () => {
    const leitor = criarLeitorSse();

    const eventos = leitor.alimentar(
      'data: {"tipo":"etapa","nome":"planejar"}\n\ndata: {"tipo":"relatorio","texto":"Oi"}\n\n',
    );

    expect(eventos).toEqual([
      { tipo: "etapa", nome: "planejar" },
      { tipo: "relatorio", texto: "Oi" },
    ]);
  });

  it("junta evento partido entre pedaços da rede", () => {
    const leitor = criarLeitorSse();

    expect(leitor.alimentar('data: {"tipo":"relatorio",')).toEqual([]);
    expect(leitor.alimentar('"texto":"## Diag')).toEqual([]);
    expect(leitor.alimentar('nóstico"}\n\n')).toEqual([{ tipo: "relatorio", texto: "## Diagnóstico" }]);
  });

  it("aceita \\r\\n como separador", () => {
    const leitor = criarLeitorSse();

    expect(leitor.alimentar('data: {"tipo":"aviso","mensagem":"x"}\r\n\r\n')).toEqual([
      { tipo: "aviso", mensagem: "x" },
    ]);
  });

  it("JSON inválido é erro de protocolo, não falha silenciosa", () => {
    const leitor = criarLeitorSse();

    expect(() => leitor.alimentar("data: {quebrado\n\n")).toThrow(ErroProtocolo);
  });
});

describe("fim do stream", () => {
  it("evento sem o \\n\\n final é entregue ao fechar", () => {
    const leitor = criarLeitorSse();

    expect(leitor.alimentar('data: {"tipo":"fim","run_id":"r","relatorio_uri":"u"}')).toEqual([]);
    expect(leitor.finalizar()).toEqual([{ tipo: "fim", run_id: "r", relatorio_uri: "u" }]);
  });
});
