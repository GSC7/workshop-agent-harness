import type { Etapa } from "../api/tipos";
import { IconeAlerta, IconeGrafico } from "./Icones";

const ROTULO_ETAPA: Record<Etapa, string> = {
  planejar: "Entendendo a pergunta",
  clarificar: "Pedindo um esclarecimento",
  perna_quantitativa: "Medindo os KPIs contra a meta",
  enriquecer: "Buscando causas e o histórico de tentativas",
  recomendar: "Validando recomendações com fonte",
  relatorio: "Escrevendo o relatório",
};

const ORDEM: Etapa[] = ["planejar", "perna_quantitativa", "enriquecer", "recomendar", "relatorio"];

/** Carregando: etapa atual do grafo + esqueleto do relatório (a tela nunca fica parada). */
export function Carregando({ etapa }: { etapa: Etapa | null }) {
  const atual = etapa ? ORDEM.indexOf(etapa) : -1;
  return (
    <div className="carregando" role="status">
      <ol className="etapas" aria-label="Progresso da análise">
        {ORDEM.map((e, i) => (
          <li key={e} data-estado={i < atual ? "feita" : i === atual ? "atual" : "pendente"}>
            {ROTULO_ETAPA[e]}
          </li>
        ))}
      </ol>
      <div className="esqueleto" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

export function ErroResposta({
  mensagem,
  aoTentarDeNovo,
}: {
  mensagem: string;
  aoTentarDeNovo?: () => void;
}) {
  return (
    <div className="erro" role="alert">
      <IconeAlerta />
      <div>
        <p className="erro-titulo">Não foi possível concluir a análise</p>
        <p>{mensagem}</p>
      </div>
      {aoTentarDeNovo && (
        <button type="button" className="botao secundario" onClick={aoTentarDeNovo}>
          Tentar de novo
        </button>
      )}
    </div>
  );
}

const SUGESTOES = [
  "Como melhorar minhas vendas no próximo mês?",
  "Como melhorar a recompra na região Sul?",
  "O que fazer com a queda do faturamento da loja física?",
  "Como aumentar a conversão do site próprio em julho?",
];

/** Vazio: o que o assistente faz + perguntas de exemplo clicáveis. */
export function Vazio({ aoEscolher }: { aoEscolher: (pergunta: string) => void }) {
  return (
    <section className="vazio" aria-labelledby="vazio-titulo">
      <IconeGrafico />
      <h2 id="vazio-titulo">Pergunte sobre suas vendas</h2>
      <p>
        O assistente mede os KPIs contra a meta, separa queda real de sazonalidade e recomenda ações
        — cada uma com a fonte que a sustenta.
      </p>
      <ul className="sugestoes">
        {SUGESTOES.map((s) => (
          <li key={s}>
            <button type="button" className="sugestao" onClick={() => aoEscolher(s)}>
              {s}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
