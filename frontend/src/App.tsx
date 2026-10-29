import { useEffect, useRef } from "react";
import { Composer } from "./componentes/Composer";
import { Vazio } from "./componentes/Estados";
import { IconeNovo } from "./componentes/Icones";
import { Resposta } from "./componentes/Resposta";
import { useChat } from "./hooks/useChat";

export function App() {
  const { turnos, emAndamento, enviar, cancelar, novaConversa } = useChat();
  const fim = useRef<HTMLDivElement>(null);
  const ultimo = turnos[turnos.length - 1];

  // Acompanha o relatório enquanto ele é escrito — só se o usuário já está perto do fim
  // (quem rolou para cima para ler não é puxado de volta) e sem animar com reduced motion.
  useEffect(() => {
    const distanciaDoFim =
      document.documentElement.scrollHeight - window.scrollY - window.innerHeight;
    if (distanciaDoFim > 160) return;
    const reduzido = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    fim.current?.scrollIntoView({ block: "end", behavior: reduzido ? "auto" : "smooth" });
  }, [turnos.length, ultimo?.relatorio.length, ultimo?.status]);

  return (
    <div className="app">
      <a href="#pergunta" className="pular">
        Ir para a pergunta
      </a>
      <header className="topo">
        <h1>Agente Analítico de Vendas</h1>
        <button
          type="button"
          className="botao secundario"
          onClick={novaConversa}
          disabled={turnos.length === 0}
        >
          <IconeNovo />
          <span>Nova conversa</span>
        </button>
      </header>
      <main className="conversa">
        {turnos.length === 0 ? (
          <Vazio aoEscolher={enviar} />
        ) : (
          turnos.map((t, i) => (
            <Resposta
              key={i}
              turno={t}
              aoTentarDeNovo={enviar}
              podeTentarDeNovo={!emAndamento && i === turnos.length - 1}
            />
          ))
        )}
        <div ref={fim} />
      </main>
      <footer className="rodape">
        <Composer emAndamento={emAndamento} aoEnviar={enviar} aoCancelar={cancelar} />
      </footer>
    </div>
  );
}
