import type { Turno } from "../estado/turno";

/** O que o agente usou: inspecionável pelo analista, recolhido por padrão (divulgação progressiva). */
export function Rastro({ turno }: { turno: Turno }) {
  const { recomendacoes, retomadas, fontesCitadas, sqls, descartadas } = turno;
  return (
    <div className="rastro">
      {recomendacoes.length + retomadas.length > 0 && (
        <details>
          <summary>
            Recomendações estruturadas ({recomendacoes.length}
            {retomadas.length > 0 && ` + ${retomadas.length} em acompanhamento`})
          </summary>
          <div className="tabela-rolavel">
          <table>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Ação</th>
                <th scope="col">KPI · dimensão</th>
                <th scope="col">Fonte</th>
              </tr>
            </thead>
            <tbody>
              {[...recomendacoes, ...retomadas].map((r) => (
                <tr key={`${r.fonte}|${r.kpi}|${r.dimensao}|${r.retomada}`}>
                  <td>{r.retomada ? "acomp." : r.prioridade}</td>
                  <td>{r.acao}</td>
                  <td>
                    {r.kpi} · {r.dimensao}
                  </td>
                  <td>
                    <code>{r.fonte}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          {descartadas > 0 && (
            <p className="nota">
              {descartadas} proposta(s) descartada(s) por não ter fonte de prescrição válida neste run.
            </p>
          )}
        </details>
      )}
      <details>
        <summary>Fontes citadas ({fontesCitadas.length})</summary>
        {fontesCitadas.length ? (
          <ul className="fontes">
            {fontesCitadas.map((f) => (
              <li key={f}>
                <code>{f}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="nota">Nenhuma fonte citada nesta resposta.</p>
        )}
      </details>
      <details>
        <summary>SQL executado ({sqls.length})</summary>
        {sqls.length ? (
          sqls.map((c, i) => (
            <figure key={i} className="sql">
              <figcaption>
                {c.kpi} por {c.eixo}
              </figcaption>
              <pre>
                <code>{c.sql}</code>
              </pre>
            </figure>
          ))
        ) : (
          <p className="nota">Nenhuma consulta foi necessária nesta resposta.</p>
        )}
      </details>
    </div>
  );
}
