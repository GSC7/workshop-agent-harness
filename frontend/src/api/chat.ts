import { criarLeitorSse } from "./sse";
import type { EventoChat } from "./tipos";

/** Falha de transporte já traduzida para o gestor; `recuperavel` = faz sentido tentar de novo. */
export class ErroChat extends Error {
  constructor(
    mensagem: string,
    readonly recuperavel: boolean,
  ) {
    super(mensagem);
  }
}

/**
 * POST /chat em SSE (via fetch: EventSource não faz POST). Cada evento completo vai para
 * `aoEvento` assim que chega — o relatório aparece enquanto é escrito.
 */
export async function enviarPergunta(
  pergunta: string,
  conversaId: string | null,
  aoEvento: (evento: EventoChat) => void,
  sinal: AbortSignal,
): Promise<void> {
  let resposta: Response;
  try {
    resposta = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ pergunta, conversa_id: conversaId }),
      signal: sinal,
    });
  } catch (erro) {
    if (erro instanceof DOMException && erro.name === "AbortError") throw erro;
    throw new ErroChat("Sem conexão com o servidor. Verifique a rede e tente de novo.", true);
  }
  if (resposta.status === 503) {
    throw new ErroChat("O assistente está indisponível no momento. Tente em instantes.", true);
  }
  if (resposta.status === 422) {
    throw new ErroChat("Não entendi o pedido. Escreva a pergunta em texto (até 2000 caracteres).", false);
  }
  if (!resposta.ok || !resposta.body) {
    throw new ErroChat(`O servidor respondeu com erro (HTTP ${resposta.status}).`, true);
  }
  const leitor = criarLeitorSse();
  const decodificador = new TextDecoder();
  const corpo = resposta.body.getReader();
  for (;;) {
    const { done, value } = await corpo.read();
    if (done) break;
    for (const evento of leitor.alimentar(decodificador.decode(value, { stream: true }))) {
      aoEvento(evento);
    }
  }
  // Bytes que o decoder segurou + último evento sem separador final.
  for (const evento of [...leitor.alimentar(decodificador.decode()), ...leitor.finalizar()]) {
    aoEvento(evento);
  }
}
