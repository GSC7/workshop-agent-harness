import { useCallback, useRef, useState } from "react";
import { ErroChat, enviarPergunta } from "../api/chat";
import { ErroProtocolo } from "../api/sse";
import { aplicarEvento, novoTurno, type Turno } from "../estado/turno";

export interface Chat {
  turnos: Turno[];
  emAndamento: boolean;
  enviar: (pergunta: string) => void;
  cancelar: () => void;
  novaConversa: () => void;
}

/** Estado da conversa: os turnos e o conversa_id que o servidor devolve no 1º evento. */
export function useChat(): Chat {
  const [turnos, setTurnos] = useState<Turno[]>([]);
  const [emAndamento, setEmAndamento] = useState(false);
  const conversaId = useRef<string | null>(null);
  const controlador = useRef<AbortController | null>(null);

  const atualizarUltimo = useCallback((f: (t: Turno) => Turno) => {
    setTurnos((atual) => {
      const ultimo = atual[atual.length - 1];
      return ultimo ? [...atual.slice(0, -1), f(ultimo)] : atual;
    });
  }, []);

  const enviar = useCallback(
    (pergunta: string) => {
      const texto = pergunta.trim();
      if (!texto || controlador.current) return; // um turno por vez na conversa
      const abortar = new AbortController();
      controlador.current = abortar;
      setEmAndamento(true);
      setTurnos((atual) => [...atual, novoTurno(texto)]);

      enviarPergunta(
        texto,
        conversaId.current,
        (evento) => {
          if (evento.tipo === "inicio") conversaId.current = evento.conversa_id;
          atualizarUltimo((t) => aplicarEvento(t, evento));
        },
        abortar.signal,
      )
        .then(() =>
          // Stream acabou sem "fim"/"erro": nunca deixar a resposta parada como se ainda viesse.
          atualizarUltimo((t) =>
            t.status === "transmitindo" || t.status === "enviando"
              ? { ...t, status: "erro", erro: "A resposta foi interrompida antes de terminar." }
              : t,
          ),
        )
        .catch((erro: unknown) => {
          if (erro instanceof DOMException && erro.name === "AbortError") {
            atualizarUltimo((t) => ({ ...t, status: "cancelado" }));
            return;
          }
          const mensagem =
            erro instanceof ErroChat || erro instanceof ErroProtocolo
              ? erro.message
              : "Algo deu errado ao receber a resposta.";
          const recuperavel = !(erro instanceof ErroChat) || erro.recuperavel;
          console.error("chat falhou:", erro);
          atualizarUltimo((t) => ({ ...t, status: "erro", erro: mensagem, recuperavel }));
        })
        .finally(() => {
          // Só libera se ainda for este envio (nova conversa pode já ter começado outro).
          if (controlador.current === abortar) {
            controlador.current = null;
            setEmAndamento(false);
          }
        });
    },
    [atualizarUltimo],
  );

  const cancelar = useCallback(() => controlador.current?.abort(), []);

  const novaConversa = useCallback(() => {
    controlador.current?.abort();
    controlador.current = null; // libera já: a próxima pergunta não pode ser ignorada
    setEmAndamento(false);
    conversaId.current = null;
    setTurnos([]);
  }, []);

  return { turnos, emAndamento, enviar, cancelar, novaConversa };
}
