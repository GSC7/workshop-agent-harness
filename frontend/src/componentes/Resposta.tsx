import Markdown from "react-markdown";
import type { Turno } from "../estado/turno";
import { Carregando, ErroResposta } from "./Estados";
import { IconeAlerta } from "./Icones";
import { Rastro } from "./Rastro";

interface Props {
  turno: Turno;
  aoTentarDeNovo: (pergunta: string) => void;
  podeTentarDeNovo: boolean;
}

export function Resposta({ turno, aoTentarDeNovo, podeTentarDeNovo }: Props) {
  const escrevendo = turno.status === "transmitindo" || turno.status === "enviando";
  const semTexto = turno.relatorio.length === 0;
  // Rastro aparece também em erro/cancelamento: o SQL que já rodou continua auditável.
  const temRastro =
    turno.sqls.length + turno.fontesCitadas.length + turno.recomendacoes.length + turno.retomadas.length > 0;
  return (
    <article className="turno" aria-busy={escrevendo}>
      <p className="pergunta">{turno.pergunta}</p>
      <div className="resposta">
        {turno.avisos.length > 0 && (
          <ul className="avisos" aria-label="Avisos da análise">
            {turno.avisos.map((a) => (
              <li key={a}>
                <IconeAlerta tamanho={16} />
                {a}
              </li>
            ))}
          </ul>
        )}
        {/* Loading só enquanto não há texto; depois o relatório cresce no lugar. */}
        {escrevendo && semTexto && <Carregando etapa={turno.etapa} />}
        {!semTexto && (
          <div className="relatorio">
            {/* react-markdown não renderiza HTML cru: texto do LLM não injeta nada na página */}
            <Markdown>{turno.relatorio}</Markdown>
            {escrevendo && <span className="cursor" aria-hidden="true" />}
          </div>
        )}
        {turno.status === "erro" && (
          <ErroResposta
            mensagem={turno.erro ?? "Erro desconhecido."}
            aoTentarDeNovo={
              podeTentarDeNovo && turno.recuperavel ? () => aoTentarDeNovo(turno.pergunta) : undefined
            }
          />
        )}
        {turno.status === "cancelado" && <p className="nota">Resposta cancelada.</p>}
        {!escrevendo && (turno.status === "concluido" || temRastro) && <Rastro turno={turno} />}
        <p className="sr-only" aria-live="polite">
          {turno.status === "concluido" ? "Relatório concluído." : ""}
        </p>
      </div>
    </article>
  );
}
