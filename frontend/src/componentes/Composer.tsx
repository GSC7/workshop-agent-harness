import { useState, type FormEvent, type KeyboardEvent } from "react";
import { IconeEnviar, IconeParar } from "./Icones";

interface Props {
  emAndamento: boolean;
  aoEnviar: (pergunta: string) => void;
  aoCancelar: () => void;
}

export function Composer({ emAndamento, aoEnviar, aoCancelar }: Props) {
  const [texto, setTexto] = useState("");
  const vazio = texto.trim().length === 0;

  const enviar = (e?: FormEvent) => {
    e?.preventDefault();
    if (vazio || emAndamento) return;
    aoEnviar(texto);
    setTexto("");
  };

  // Enter envia; Shift+Enter quebra linha.
  const aoTeclar = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // isComposing: no teclado IME, Enter confirma a composição — não envia.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) enviar(e);
  };

  return (
    <form className="composer" onSubmit={enviar}>
      <label htmlFor="pergunta" className="sr-only">
        Sua pergunta
      </label>
      <textarea
        id="pergunta"
        rows={2}
        maxLength={2000}
        placeholder="Ex.: Como melhorar a recompra na região Sul no próximo mês?"
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onKeyDown={aoTeclar}
      />
      {emAndamento ? (
        <button type="button" className="botao" onClick={aoCancelar} aria-label="Parar a resposta">
          <IconeParar />
          <span>Parar</span>
        </button>
      ) : (
        <button type="submit" className="botao primario" disabled={vazio} aria-label="Enviar pergunta">
          <IconeEnviar />
          <span>Enviar</span>
        </button>
      )}
    </form>
  );
}
