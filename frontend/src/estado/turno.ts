import type {
  ConsultaSql,
  Etapa,
  EventoChat,
  FonteRecuperada,
  Premissas,
  Recomendacao,
} from "../api/tipos";

export type StatusTurno = "enviando" | "transmitindo" | "concluido" | "erro" | "cancelado";

/** Uma pergunta e tudo que o agente devolveu para ela (o rastro fica inspecionável). */
export interface Turno {
  pergunta: string;
  status: StatusTurno;
  etapa: Etapa | null;
  relatorio: string;
  premissas: Premissas | null;
  sqls: ConsultaSql[];
  fontesRecuperadas: FonteRecuperada[];
  fontesCitadas: string[];
  recomendacoes: Recomendacao[];
  retomadas: Recomendacao[];
  descartadas: number;
  avisos: string[];
  erro: string | null;
  /** Falso quando repetir a mesma pergunta falharia igual (ex.: 422). */
  recuperavel: boolean;
  runId: string | null;
  relatorioUri: string | null;
}

export function novoTurno(pergunta: string): Turno {
  return {
    pergunta,
    status: "enviando",
    etapa: null,
    relatorio: "",
    premissas: null,
    sqls: [],
    fontesRecuperadas: [],
    fontesCitadas: [],
    recomendacoes: [],
    retomadas: [],
    descartadas: 0,
    avisos: [],
    erro: null,
    recuperavel: true,
    runId: null,
    relatorioUri: null,
  };
}

/** Reducer puro: cada evento SSE vira um novo turno (o anterior não é mutado). */
export function aplicarEvento(turno: Turno, evento: EventoChat): Turno {
  switch (evento.tipo) {
    case "inicio":
      return { ...turno, status: "transmitindo", runId: evento.run_id };
    case "etapa":
      return { ...turno, etapa: evento.nome };
    case "sql":
      return { ...turno, sqls: [...turno.sqls, { kpi: evento.kpi, eixo: evento.eixo, sql: evento.sql }] };
    case "premissas":
      return { ...turno, premissas: evento.premissas };
    case "aviso":
      return turno.avisos.includes(evento.mensagem)
        ? turno
        : { ...turno, avisos: [...turno.avisos, evento.mensagem] };
    case "fontes":
      return { ...turno, fontesRecuperadas: evento.fontes };
    case "recomendacoes":
      return { ...turno, recomendacoes: evento.recomendacoes };
    case "retomadas":
      return { ...turno, retomadas: evento.retomadas };
    case "recomendacoes_descartadas":
      return { ...turno, descartadas: turno.descartadas + evento.itens.length };
    case "relatorio":
      return { ...turno, relatorio: turno.relatorio + evento.texto };
    case "fontes_citadas":
      return { ...turno, fontesCitadas: evento.fontes };
    case "fim":
      return { ...turno, status: "concluido", relatorioUri: evento.relatorio_uri };
    case "erro":
      return { ...turno, status: "erro", erro: evento.mensagem };
    case "plano":
      return turno;
    default:
      // Evento que esta versão do front não conhece (backend mais novo): ignora, não quebra.
      return turno;
  }
}
