// Contrato dos eventos SSE do POST /chat (backend/agent/grafo.py e app/services/chat.py).
// União discriminada por `tipo`: o compilador garante que cada evento é tratado com sua forma.

export interface Recomendacao {
  prioridade: number;
  kpi: string;
  dimensao: string;
  acao: string;
  justificativa: string;
  fonte: string;
  retomada: boolean;
}

export interface FonteRecuperada {
  fonte: string;
  colecao?: string;
  resultado?: string | null;
  periodo_referencia?: string;
  subtipo?: string;
}

export interface Premissas {
  periodo_alvo: string;
  origem_periodo: string;
  kpis: string[];
  eixos: string[];
  valor: string | null;
  herdado: string[];
  dados_ate: string | null;
  janela_recente: string | null;
}

export type Etapa =
  | "planejar"
  | "clarificar"
  | "perna_quantitativa"
  | "enriquecer"
  | "recomendar"
  | "relatorio";

export type EventoChat =
  | { tipo: "inicio"; run_id: string; conversa_id: string }
  | { tipo: "etapa"; nome: Etapa }
  | { tipo: "plano"; plano: Record<string, unknown> }
  | { tipo: "sql"; kpi: string; eixo: string; sql: string }
  | { tipo: "premissas"; premissas: Premissas }
  | { tipo: "aviso"; mensagem: string }
  | { tipo: "fontes"; fontes: FonteRecuperada[] }
  | { tipo: "recomendacoes"; recomendacoes: Recomendacao[] }
  | { tipo: "retomadas"; retomadas: Recomendacao[] }
  | { tipo: "recomendacoes_descartadas"; itens: { motivo: string; fonte: string }[] }
  | { tipo: "relatorio"; texto: string }
  | { tipo: "fontes_citadas"; fontes: string[] }
  | { tipo: "fim"; run_id: string; relatorio_uri: string }
  | { tipo: "erro"; run_id: string; mensagem: string };

export interface ConsultaSql {
  kpi: string;
  eixo: string;
  sql: string;
}
