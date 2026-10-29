import type { EventoChat } from "./tipos";

/** Resposta do servidor fora do contrato SSE: falha visível, nunca descartada em silêncio. */
export class ErroProtocolo extends Error {}

export interface LeitorSse {
  /** Recebe um pedaço do corpo e devolve os eventos que ficaram completos. */
  alimentar(pedaco: string): EventoChat[];
  /** Fim do corpo: entrega o último evento, mesmo sem o separador final. */
  finalizar(): EventoChat[];
}

/** Parser incremental: um evento pode chegar partido entre pedaços da rede. */
function decodificar(blocos: string[]): EventoChat[] {
  const eventos: EventoChat[] = [];
  for (const bloco of blocos) {
    const dados = bloco
      .split("\n")
      .filter((linha) => linha.startsWith("data:"))
      .map((linha) => linha.slice(5).trimStart())
      .join("\n");
    if (!dados) continue;
    try {
      eventos.push(JSON.parse(dados) as EventoChat);
    } catch {
      throw new ErroProtocolo("o servidor enviou um evento em formato inválido");
    }
  }
  return eventos;
}

export function criarLeitorSse(): LeitorSse {
  let pendente = "";
  return {
    alimentar(pedaco: string): EventoChat[] {
      pendente += pedaco.replace(/\r\n/g, "\n");
      const blocos = pendente.split("\n\n");
      pendente = blocos.pop() ?? "";
      return decodificar(blocos);
    },
    finalizar(): EventoChat[] {
      const resto = pendente;
      pendente = "";
      return decodificar([resto]);
    },
  };
}
